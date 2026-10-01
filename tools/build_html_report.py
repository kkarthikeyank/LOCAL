"""
Builds an HTML rendering of the validation report -- the same content that
check_refs.py's build_report() puts in the .docx (Execution Summary,
Validation Results, Resource Counts, Placeholder-Looking IDs, Orphan
Connectivity Checks, Orphan Details) -- for inlining directly into the
notification email body, so the reader doesn't have to open the attachment
to see the results.

Reads only the CSVs check_refs.py already writes, from reports/. Does not
touch validation logic -- purely a read-only presentation layer.
"""
import csv
import glob
import html
import os

REPORTS_DIR = "reports"
EXPECTED_ORPHAN_TYPES = {"payer", "network", "ntwk"}
ORPHAN_TABLE_CAP = 100  # keep the email itself from becoming enormous


def is_expected_orphan_type(type_str):
    low = (type_str or "").lower()
    return any(t in low for t in EXPECTED_ORPHAN_TYPES)


def read_csv(path):
    if not os.path.exists(path):
        return [], []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return (rows[0], rows[1:]) if rows else ([], [])


def e(text):
    return html.escape(str(text))


def table(headers, rows, cap=None):
    shown = rows[:cap] if cap else rows
    out = ['<table style="border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:13px">']
    out.append("<tr>" + "".join(
        f'<th style="text-align:left;border:1px solid #ddd;padding:6px;background:#1f4e78;color:#fff">{e(h)}</th>'
        for h in headers) + "</tr>")
    for row in shown:
        out.append("<tr>" + "".join(
            f'<td style="border:1px solid #ddd;padding:6px">{e(v)}</td>' for v in row) + "</tr>")
    out.append("</table>")
    if cap and len(rows) > cap:
        out.append(f'<p style="font-size:12px;color:#666">... {len(rows) - cap:,} more rows not '
                    f'shown here -- see the attached report for the full list.</p>')
    return "".join(out)


def discover_contracts():
    return sorted({
        os.path.basename(p).replace("dangling_summary_", "").replace(".csv", "")
        for p in glob.glob(os.path.join(REPORTS_DIR, "dangling_summary_*.csv"))
    })


def build_html_report(contracts=None, reports_dir=None):
    global REPORTS_DIR
    if reports_dir:                 # LOCAL repo: each script writes into its own report folder
        REPORTS_DIR = reports_dir
    contracts = contracts or discover_contracts()
    if not contracts:
        return ""

    parts = ['<h2 style="font-family:Arial,sans-serif">Reference Integrity Validation Report</h2>']

    for c in contracts:
        _, srows = read_csv(os.path.join(REPORTS_DIR, f"dangling_summary_{c}.csv"))
        _, crows = read_csv(os.path.join(REPORTS_DIR, f"resource_counts_{c}.csv"))
        _, prows = read_csv(os.path.join(REPORTS_DIR, f"placeholder_refs_{c}.csv"))
        _, orows = read_csv(os.path.join(REPORTS_DIR, f"orphan_refs_{c}.csv"))

        total_dangling = sum(int(r[5]) for r in srows) if srows else 0
        total_affected = sum(int(r[6]) for r in srows) if srows else 0
        status = "PASS" if total_dangling == 0 else "FAIL"

        parts.append(f'<h3 style="font-family:Arial,sans-serif;border-bottom:2px solid #1f4e78;'
                     f'padding-bottom:4px">Contract: {e(c)}</h3>')

        parts.append('<h4 style="font-family:Arial,sans-serif">1. Execution Summary</h4>')
        parts.append(table(["Metric", "Result"], [
            ["Validation Type", "Forward Reference / Dangling Reference"],
            ["Relationship Checks", len(srows)],
            ["Dangling References", f"{total_dangling:,}"],
            ["Affected Source Resources", f"{total_affected:,}"],
            ["Reference Integrity", status],
        ]))

        parts.append('<h4 style="font-family:Arial,sans-serif">2. Validation Results</h4>')
        vrows = []
        for row in srows:
            src_type, field, tgt_type, tgt_pub, total, bad, aff = row[:7]
            vrows.append([src_type, field, tgt_type, f"{int(total):,}", bad, aff,
                          "PASS" if int(bad) == 0 else "FAIL"])
        parts.append(table(
            ["Source Type", "Field", "Target Type", "Total Refs", "Dangling", "Affected", "Result"],
            vrows))

        if crows:
            parts.append('<h4 style="font-family:Arial,sans-serif">3. Resource Counts</h4>')
            parts.append(table(["Resource Type", "Unique Count", "Raw Count"], crows))

        if prows:
            ph_by_type = {}
            for row in prows:
                tgt_type, connected = row[4], row[6]
                d = ph_by_type.setdefault(tgt_type, {"total": 0, "connected": 0})
                d["total"] += 1
                if connected == "yes":
                    d["connected"] += 1
            parts.append('<h4 style="font-family:Arial,sans-serif">4. Placeholder-Looking Target IDs</h4>')
            parts.append(table(
                ["Target Type", "Placeholder-Looking", "Connected", "Dangling"],
                [[t, d["total"], d["connected"], d["total"] - d["connected"]]
                 for t, d in sorted(ph_by_type.items())]))

        parts.append('<h4 style="font-family:Arial,sans-serif">5. Orphan Connectivity Checks</h4>')
        conn_checks = {}
        for r in orows:
            orphan_type, _, _, _, otype, flag = r[0], r[1], r[2], r[3], r[4], r[5]
            ref_type, field = r[6], r[7]
            key = (orphan_type, ref_type, field)
            d = conn_checks.setdefault(key, {"count": 0, "types": {}, "review": []})
            d["count"] += 1
            if otype:
                d["types"][otype] = d["types"].get(otype, 0) + 1
            if flag == "Review":
                d["review"].append(r)

        if conn_checks:
            lines = []
            for (orphan_type, ref_type, field), d in sorted(conn_checks.items()):
                type_note = ""
                if d["types"]:
                    breakdown = ", ".join(f"{t}: {n}" for t, n in sorted(d["types"].items()))
                    type_note = f" -- type(s): {breakdown}"
                if d["review"]:
                    line_status = f"REVIEW ({len(d['review'])} of {d['count']} not an expected type)"
                else:
                    line_status = "Pass"
                lines.append(f'<p style="font-family:Arial,sans-serif;font-size:13px;margin:4px 0">'
                             f'{e(orphan_type)} is connected to {e(ref_type)}.{e(field)} - '
                             f'<strong>{e(line_status)}</strong> ({d["count"]:,} orphaned{e(type_note)})</p>')
            parts.extend(lines)
            parts.append('<p style="font-family:Arial,sans-serif;font-size:12px;color:#666">'
                         "Orphans of an expected type (Payer, Network) are normal -- those Organization "
                         "levels aren't required to link via an OrganizationAffiliation record. Any REVIEW "
                         "line lists orphans of an unexpected type that likely need a data fix.</p>")
        else:
            parts.append('<p style="font-family:Arial,sans-serif;font-size:13px">Every published resource '
                         "is referenced back at least once by its expected relationship - Pass.</p>")

        if orows:
            parts.append('<h4 style="font-family:Arial,sans-serif">6. Orphan Details</h4>')
            parts.append(table(
                ["Orphan Type", "Orphan ID", "Identifier", "Name", "Type", "Flag",
                 "Expected Ref Type", "Expected Field"],
                orows, cap=ORPHAN_TABLE_CAP))

    return "".join(parts)
