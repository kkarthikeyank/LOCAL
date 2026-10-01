"""
Find dangling FHIR references (e.g. OrganizationAffiliation -> Location/4136336 that doesn't exist).

Usage:
    python check_hy.py H1619        # one contract (reads ./H1619/*.json)
    python check_hy.py              # all contracts

Reads each contract's bundle files straight from its local folder
(<script_dir>/<CONTRACT>/*.json) -- no network/index download involved, then:
  Pass 1 - build a set of every (resourceType, id) that actually exists.
  Pass 2 - walk every resource for "reference" fields and flag targets missing from that set.

Outputs (per contract):
  dangling_refs_<C>.csv      one row per broken reference
  dangling_summary_<C>.csv   affected counts + % per source type -> target type
"""
import json, csv, re, os, sys, glob



# Prefix-agnostic: matches jhp-H1619-2027-location-part1.json AND any other org's
# <prefix>-<contract>-<year>-<category>-part<n>.json layout (e.g. CHPW's H5826 files).
FNAME_RE = re.compile(r"(?P<contract>[hH]\d+)-(?P<year>\d+)-(?P<category>[a-z]+)-part(?P<part>\d+)\.json", re.I)
# "Location/4136336", "Location/4136336/_history/1", or an absolute URL ending in Type/id
REF_RE = re.compile(r"(?:^|/)(?P<type>[A-Z][A-Za-z]+)/(?P<id>[A-Za-z0-9\-.]{1,64})(?:/_history/.*)?$")

PLACEHOLDER_EXT_URL = "http://hapifhir.io/fhir/StructureDefinition/resource-placeholder"


def has_placeholder_extension(resource):
    """True if the resource itself is explicitly marked placeholder data via the
    official 'resource-placeholder' extension (valueBoolean=true) -- the authoritative
    signal, as opposed to guessing from the shape of the id (see is_placeholder_id)."""
    for ext in resource.get("extension", []) or []:
        if ext.get("url") == PLACEHOLDER_EXT_URL and ext.get("valueBoolean") is True:
            return True
    return False

PLACEHOLDER_LITERALS = {
    "test", "example", "unknown", "tbd", "n/a", "na", "none", "null",
    "sample", "dummy", "fake", "todo", "xxx", "0000000000",
}


def is_placeholder_id(rid):
    """True if a target id looks like dummy/test data rather than a real FHIR id
    (all-zero, all-same-digit, simple sequential digits, or a known dummy literal),
    even though it may still happen to match a real resource in `existing`."""
    s = str(rid).strip()
    low = s.lower()
    if low in PLACEHOLDER_LITERALS:
        return True
    if s.isdigit():
        if len(set(s)) == 1:          # e.g. "0000", "1111"
            return True
        asc = "".join(str((int(s[0]) + i) % 10) for i in range(len(s)))
        desc = "".join(str((int(s[0]) - i) % 10) for i in range(len(s)))
        if len(s) >= 4 and s in (asc, desc):   # e.g. "1234", "9876"
            return True
    return False


def local_files_for(folder):
    """Every *.json file sitting in <folder>, sorted for stable output."""
    return sorted(glob.glob(os.path.join(folder, "*.json")))


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def resources_of(raw):
    """Yield each FHIR resource from a Bundle (or a bare list/object)."""
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("resourceType") == "Bundle":
        for e in data.get("entry", []):
            r = e.get("resource", e)
            if isinstance(r, dict):
                yield r
    elif isinstance(data, list):
        for r in data:
            if isinstance(r, dict):
                yield r
    elif isinstance(data, dict):
        yield data


def find_refs(node, path=""):
    """Recursively yield (json_path, reference_string) for every 'reference' field."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if k == "reference" and isinstance(v, str):
                yield p, v
            else:
                yield from find_refs(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from find_refs(v, f"{path}[{i}]")


def field_root(path):
    """'location[0].reference' -> 'location'; 'coverageArea[3].reference' -> 'coverageArea'.
    Different fields mean different things (location vs coverageArea), so they are
    reported separately rather than collapsed into one source->target row."""
    p = re.sub(r"\[\d+\]", "", path)
    if p.endswith(".reference"):
        p = p[: -len(".reference")]
    return p or "reference"


def parse_ref(ref):
    """Return (type, id) for a resolvable reference, else None for contained/urn refs."""
    if not ref or ref.startswith("#") or ref.startswith("urn:"):
        return None
    m = REF_RE.search(ref.strip())
    if not m:
        return None
    return m.group("type"), m.group("id")


def run(contract, folder):
    print(f"\n=== {contract} ===", flush=True)
    paths = local_files_for(folder)
    if not paths:
        print(f"No *.json files found in {folder} -- skipping {contract}.", flush=True)
        return 0
    print(f"{len(paths)} local files in {folder}", flush=True)

    files = []  # (path, fname, category, part)
    for path in paths:
        fname = os.path.basename(path)
        m = FNAME_RE.search(fname)
        files.append((path, fname,
                      m.group("category") if m else "unknown",
                      int(m.group("part")) if m else 0))

    # ---- Pass 1: every (resourceType, id) that exists ----
    print("Pass 1: indexing existing resource ids ...", flush=True)
    existing = set()
    identifiers = {}              # (resourceType, id) -> "system|value" identifiers, joined by "; "
    raw_type_counts = {}          # includes duplicate ids (raw entry count)
    ext_placeholder_by_file = {}  # fname -> count of resources with resource-placeholder ext
    for path, fname, category, part in files:
        raw = read_bytes(path)
        n = 0
        for r in resources_of(raw):
            rt, rid = r.get("resourceType"), r.get("id")
            if rt and rid is not None and str(rid) != "":
                existing.add((rt, str(rid)))
                raw_type_counts[rt] = raw_type_counts.get(rt, 0) + 1
                idents = [f"{i.get('system', '')}|{i.get('value', '')}"
                          for i in (r.get("identifier") or []) if isinstance(i, dict)]
                if idents:
                    identifiers[(rt, str(rid))] = "; ".join(idents)
            if has_placeholder_extension(r):
                ext_placeholder_by_file[fname] = ext_placeholder_by_file.get(fname, 0) + 1
            n += 1
        print(f"  {fname}: {n}", flush=True)

    if ext_placeholder_by_file:
        print(f"\nResources marked with resource-placeholder extension, by file:")
        for fname in sorted(ext_placeholder_by_file):
            print(f"  {fname}: {ext_placeholder_by_file[fname]:,}")
        print(f"Total: {sum(ext_placeholder_by_file.values()):,}")
    # Unique count per type = correct denominator for "% of source affected".
    type_counts = {}
    for t, _ in existing:
        type_counts[t] = type_counts.get(t, 0) + 1
    published_types = set(type_counts)
    dups = sum(raw_type_counts[t] - type_counts.get(t, 0) for t in raw_type_counts)
    print(f"Indexed {len(existing)} unique resources"
          f"{f' ({dups} duplicate ids collapsed)' if dups else ''}.", flush=True)

    # ---- Pass 2: check every reference ----
    print("Pass 2: checking references ...", flush=True)
    dangling = []                 # source_type, source_id, file, field, target_type, target_id
    affected = {}                 # (src_type, field, tgt_type) -> source ids with >=1 broken ref
    ref_totals = {}               # (src_type, field, tgt_type) -> total refs seen
    src_with_any_ref = {}         # (src_type, field, tgt_type) -> source ids having such a ref
    bad_ref_counts = {}           # (src_type, field, tgt_type) -> broken ref count
    placeholder_refs = []         # src_type, src_id, file, field, target_type, target_id, connected(bool)
    placeholder_connected = {}    # (src_type, field, tgt_type) -> count where placeholder id resolved
    placeholder_total = {}        # (src_type, field, tgt_type) -> count of placeholder-looking ids seen
    referenced_ids = {}           # (src_type, field, tgt_type) -> set of target ids referenced (any status)

    for path, fname, category, part in files:
        raw = read_bytes(path)
        for r in resources_of(raw):
            src_type, src_id = r.get("resourceType", ""), str(r.get("id", ""))
            for field, ref in find_refs(r):
                parsed = parse_ref(ref)
                if not parsed:
                    continue
                tgt_type, tgt_id = parsed
                key = (src_type, field_root(field), tgt_type)
                ref_totals[key] = ref_totals.get(key, 0) + 1
                src_with_any_ref.setdefault(key, set()).add(src_id)
                referenced_ids.setdefault(key, set()).add(tgt_id)
                connected = (tgt_type, tgt_id) in existing
                if not connected:
                    dangling.append([src_type, src_id, fname, field, tgt_type, tgt_id, ref])
                    affected.setdefault(key, set()).add(src_id)
                    bad_ref_counts[key] = bad_ref_counts.get(key, 0) + 1
                if is_placeholder_id(tgt_id):
                    placeholder_refs.append([src_type, src_id, fname, field, tgt_type, tgt_id,
                                              "yes" if connected else "no"])
                    placeholder_total[key] = placeholder_total.get(key, 0) + 1
                    if connected:
                        placeholder_connected[key] = placeholder_connected.get(key, 0) + 1

    # ---- Write per-reference detail ----
    with open(f"dangling_refs_{contract}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_type", "source_id", "source_file", "field_path",
                    "target_type", "target_id", "raw_reference"])
        w.writerows(dangling)

    # ---- Write placeholder-id detail (dummy-looking ids, connected or not) ----
    with open(f"placeholder_refs_{contract}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_type", "source_id", "source_file", "field_path",
                    "target_type", "target_id", "connected"])
        w.writerows(placeholder_refs)

    # ---- Write summary (per source.field -> target) ----
    rows = []
    for key in sorted(set(list(ref_totals) + list(affected))):
        src_type, field, tgt_type = key
        n_bad_refs = bad_ref_counts.get(key, 0)
        n_affected = len(affected.get(key, ()))
        n_src_total = type_counts.get(src_type, 0)
        n_src_with_ref = len(src_with_any_ref.get(key, ()))
        pct_of_all = (100.0 * n_affected / n_src_total) if n_src_total else 0.0
        pct_of_linked = (100.0 * n_affected / n_src_with_ref) if n_src_with_ref else 0.0
        # "yes" = target type is published but specific ids are missing;
        # "no"  = that whole resource type was never published in this contract.
        tgt_published = "yes" if tgt_type in published_types else "no"
        rows.append([src_type, field, tgt_type, tgt_published, ref_totals.get(key, 0),
                     n_bad_refs, n_affected, n_src_total, f"{pct_of_all:.2f}",
                     n_src_with_ref, f"{pct_of_linked:.2f}"])

    with open(f"dangling_summary_{contract}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_type", "field", "target_type", "target_type_published",
                    "total_refs", "dangling_refs",
                    "affected_source_resources", "total_source_resources", "pct_of_all_source",
                    "source_resources_with_this_ref", "pct_of_linked_source"])
        w.writerows(rows)

    # ---- Console report ----
    print(f"\n--- {contract} dangling references (by field) ---")
    hdr = (f"{'SOURCE.FIELD':<42} {'TARGET':<18} {'PUB':>4} {'REFS':>10} "
           f"{'BROKEN':>10} {'AFFECTED':>9} {'%TYPE':>7}")
    print(hdr)
    print("-" * len(hdr))
    for (src_type, field, tgt_type, tgt_pub, total, bad,
         aff, src_total, pct, _, _) in rows:
        if bad:
            print(f"{src_type + '.' + field:<42} {tgt_type:<18} {tgt_pub:>4} {total:>10,} "
                  f"{bad:>10,} {aff:>9,} {pct:>6}%")
    clean = [r for r in rows if not r[5]]
    if clean:
        print("\nClean (no dangling refs):")
        for src_type, field, tgt_type, *_ in clean:
            print(f"  {src_type}.{field} -> {tgt_type}: all resolve")
    missing_types = sorted({r[2] for r in rows if r[3] == "no" and r[5]})
    if missing_types:
        print(f"\nTarget types referenced but NEVER published: {', '.join(missing_types)}")

    if placeholder_refs:
        print(f"\n--- {contract} placeholder-looking target ids ---")
        phdr = f"{'SOURCE.FIELD':<42} {'TARGET':<18} {'PLACEHOLDER':>11} {'CONNECTED':>10}"
        print(phdr)
        print("-" * len(phdr))
        for key in sorted(set(list(placeholder_total))):
            src_type, field, tgt_type = key
            tot = placeholder_total.get(key, 0)
            conn = placeholder_connected.get(key, 0)
            print(f"{src_type + '.' + field:<42} {tgt_type:<18} {tot:>11,} {conn:>10,}")

        # Roll up by target resource type, across every field that references it.
        by_type_total, by_type_conn = {}, {}
        for row in placeholder_refs:
            _, _, _, _, tgt_type, _, connected = row
            by_type_total[tgt_type] = by_type_total.get(tgt_type, 0) + 1
            if connected == "yes":
                by_type_conn[tgt_type] = by_type_conn.get(tgt_type, 0) + 1
        print(f"\nPlaceholder counts by resource type (all fields combined):")
        thdr = f"{'RESOURCE TYPE':<20} {'PLACEHOLDER':>11} {'CONNECTED':>10} {'DANGLING':>9}"
        print(thdr)
        print("-" * len(thdr))
        for tgt_type in sorted(by_type_total):
            tot = by_type_total[tgt_type]
            conn = by_type_conn.get(tgt_type, 0)
            print(f"{tgt_type:<20} {tot:>11,} {conn:>10,} {tot - conn:>9,}")

        n_conn = sum(placeholder_connected.values())
        n_not_conn = len(placeholder_refs) - n_conn
        print(f"\nTotal placeholder-looking ids: {len(placeholder_refs):,} "
              f"({n_conn:,} still resolve to a real resource, "
              f"{n_not_conn:,} are dangling)")

        if n_not_conn:
            print(f"\nNOT CONNECTED placeholder ids (dangling): {n_not_conn:,}")
            not_conn_by_type = {}
            for row in placeholder_refs:
                _, _, _, _, tgt_type, _, connected = row
                if connected == "no":
                    not_conn_by_type[tgt_type] = not_conn_by_type.get(tgt_type, 0) + 1
            for tgt_type in sorted(not_conn_by_type):
                print(f"  {tgt_type}: {not_conn_by_type[tgt_type]:,}")

        # Placeholder count per source JSON file, standalone (no connected/dangling split).
        by_file = {}
        for row in placeholder_refs:
            fname = row[2]
            by_file[fname] = by_file.get(fname, 0) + 1
        print(f"\nPlaceholder count by source file:")
        for fname in sorted(by_file):
            print(f"  {fname}: {by_file[fname]:,}")

        # End-to-end: InsurancePlan -> Organization specifically (its own chain,
        # not mixed in with every other source type that also references Organization).
        ip_org = [r for r in placeholder_refs
                  if r[0] == "InsurancePlan" and r[4] == "Organization"]
        if ip_org:
            ip_conn = sum(1 for r in ip_org if r[6] == "yes")
            ip_not_conn = len(ip_org) - ip_conn
            print(f"\nEnd-to-end: InsurancePlan -> Organization placeholder ids: "
                  f"{len(ip_org):,} total ({ip_conn:,} connected, {ip_not_conn:,} not connected)")
            for src_type, src_id, fname, field, tgt_type, tgt_id, connected in ip_org:
                print(f"  InsurancePlan/{src_id} --{field}--> Organization/{tgt_id} "
                      f"[{('connected' if connected == 'yes' else 'NOT CONNECTED')}]")

        # End-to-end: OrganizationAffiliation -> Organization specifically.
        oa_org = [r for r in placeholder_refs
                  if r[0] == "OrganizationAffiliation" and r[4] == "Organization"]
        if oa_org:
            oa_conn = sum(1 for r in oa_org if r[6] == "yes")
            oa_not_conn = len(oa_org) - oa_conn
            print(f"\nEnd-to-end: OrganizationAffiliation -> Organization placeholder ids: "
                  f"{len(oa_org):,} total ({oa_conn:,} connected, {oa_not_conn:,} not connected)")
            for src_type, src_id, fname, field, tgt_type, tgt_id, connected in oa_org:
                print(f"  OrganizationAffiliation/{src_id} --{field}--> Organization/{tgt_id} "
                      f"[{('connected' if connected == 'yes' else 'NOT CONNECTED')}]")

        # End-to-end: PractitionerRole -> Practitioner specifically.
        pr_prac = [r for r in placeholder_refs
                   if r[0] == "PractitionerRole" and r[4] == "Practitioner"]
        if pr_prac:
            pr_conn = sum(1 for r in pr_prac if r[6] == "yes")
            pr_not_conn = len(pr_prac) - pr_conn
            print(f"\nEnd-to-end: PractitionerRole -> Practitioner placeholder ids: "
                  f"{len(pr_prac):,} total ({pr_conn:,} connected, {pr_not_conn:,} not connected)")
            for src_type, src_id, fname, field, tgt_type, tgt_id, connected in pr_prac:
                print(f"  PractitionerRole/{src_id} --{field}--> Practitioner/{tgt_id} "
                      f"[{('connected' if connected == 'yes' else 'NOT CONNECTED')}]")

    # ---- Reverse/orphan checks: published resources never referenced back ----
    # e.g. an Organization with no OrganizationAffiliation.organization pointing to it,
    # or a Practitioner with no PractitionerRole.practitioner pointing to it.
    ORPHAN_CHECKS = [
        ("Organization", "OrganizationAffiliation", "organization"),
        ("Practitioner", "PractitionerRole", "practitioner"),
    ]
    orphan_rows = []
    for tgt_type, ref_src_type, field in ORPHAN_CHECKS:
        key = (ref_src_type, field, tgt_type)
        referenced = referenced_ids.get(key, set())
        all_ids = {rid for (rt, rid) in existing if rt == tgt_type}
        orphans = sorted(all_ids - referenced)
        if not all_ids:
            continue
        print(f"\n--- {contract}: {tgt_type} not referenced by any {ref_src_type}.{field} ---")
        print(f"{len(orphans):,} of {len(all_ids):,} {tgt_type} resources are orphaned "
              f"({100.0 * len(orphans) / len(all_ids):.2f}%)")
        for oid in orphans:
            orphan_rows.append([tgt_type, oid, identifiers.get((tgt_type, oid), ""),
                                 ref_src_type, field])

    if orphan_rows:
        with open(f"orphan_refs_{contract}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["orphan_type", "orphan_id", "identifier",
                        "expected_referencing_type", "expected_field"])
            w.writerows(orphan_rows)

    print(f"\nTotal dangling references: {len(dangling):,}")
    print(f"Wrote: dangling_refs_{contract}.csv, dangling_summary_{contract}.csv"
          + (f", placeholder_refs_{contract}.csv" if placeholder_refs else "")
          + (f", orphan_refs_{contract}.csv" if orphan_rows else ""))
    return len(dangling)


CONTRACT_DIR_RE = re.compile(r"^[hH]\d+$")


def discover_contracts(base_dir):
    """Every subfolder of base_dir named like a contract id (H1619, H5826, ...)
    that actually holds at least one *.json bundle, mapped to its full path."""
    contracts = {}
    for name in sorted(os.listdir(base_dir)):
        full = os.path.join(base_dir, name)
        if os.path.isdir(full) and CONTRACT_DIR_RE.match(name):
            if glob.glob(os.path.join(full, "*.json")):
                contracts[name.upper()] = full
    return contracts


# CLI: python check_hy.py [CONTRACT]
#   CONTRACT  run just one contract, e.g. H1619 (default: all contracts found
#             as sibling folders of this script, e.g. ./H1619/*.json)
SCRIPT_DIR = os.environ.get("INPUT_DIR") or os.path.dirname(os.path.abspath(__file__))  # INPUT_DIR: set by GitHub Actions runner
CONTRACTS = discover_contracts(SCRIPT_DIR)

args = [a for a in sys.argv[1:]]
positional = [a for a in args if not a.startswith("-")]
arg = positional[0].upper() if positional else None

if not CONTRACTS:
    print(f"No contract folders with *.json files found under {SCRIPT_DIR}.")
    print("Expected e.g. .\\H1619\\*.json next to this script.")
    sys.exit(1)

if arg:
    if arg not in CONTRACTS:
        print(f"Unknown/empty contract '{arg}'. Choose from: {', '.join(CONTRACTS)}")
        sys.exit(1)
    targets = {arg: CONTRACTS[arg]}
else:
    targets = CONTRACTS

grand_total = 0
for c, folder in targets.items():
    grand_total += run(c, folder)
if len(targets) > 1:
    print(f"\n==== ALL CONTRACTS: {grand_total:,} total dangling references ====")
sys.exit(1 if grand_total else 0)  # non-zero exit = dangling references found (GitHub Actions FAIL)
