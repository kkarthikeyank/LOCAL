"""
Run every validation script in scripts/ against one contract's ZIP.

    python tools/run_validations.py H1625 [--only check_hyLOCAL.py]

1. locate input/<C>/<C>.zip           5. run each script (a failure never stops the rest)
2. extract to work/<C>/extracted      6. collect each script's reports into
3. find *.json recursively               reports/<C>/run_<UTC stamp>/<script>/
4. stage them as work/<C>/data/<C>/   7. write results.json (read by summary / email tools)

Always exits 0 once results.json is written; the workflow decides pass/fail from it.
Nothing under input/ or reports/ is ever deleted.
"""
import fnmatch
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def safe_extract(zip_path, dest):
    """Extract, refusing any member that would land outside dest (zip-slip)."""
    dest_abs = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        if bad:
            raise zipfile.BadZipFile("corrupt member: %s" % bad)
        for m in z.infolist():
            target = os.path.realpath(os.path.join(dest, m.filename))
            if target != dest_abs and not target.startswith(dest_abs + os.sep):
                raise ValueError("unsafe path in ZIP: %s" % m.filename)
        z.extractall(dest)


def find_json(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != "__MACOSX"]
        out += [os.path.join(dp, f) for f in fn
                if f.lower().endswith(".json") and not f.startswith("._")]
    return sorted(out)


def stage_json(files, contract_dir):
    """Flatten into <contract_dir>/*.json (what the scripts expect). Name clashes get a suffix."""
    os.makedirs(contract_dir, exist_ok=True)
    used = set()
    for src in files:
        name = os.path.basename(src)
        stem, ext = os.path.splitext(name)
        n = 1
        while name.lower() in used:
            n += 1
            name = "%s__%d%s" % (stem, n, ext)
        used.add(name.lower())
        shutil.move(src, os.path.join(contract_dir, name))


def discover_scripts(cfg):
    paths = [p for p in glob.glob(os.path.join(ROOT, "scripts", "*.py"))
             if not os.path.basename(p).startswith("_")]
    items = []
    for p in paths:
        name = os.path.basename(p)
        c = cfg.get(name, {})
        items.append({"name": name, "path": p, "label": c.get("label", name),
                      "args": c.get("args", []), "attach": c.get("attach", []), "dynamic": c.get("dynamic_report", []),
                      "order": c.get("order", 1000)})
    items.sort(key=lambda i: (i["order"], i["name"]))
    return items


def list_files(folder):
    out = []
    for dp, _, fn in os.walk(folder):
        out += [os.path.relpath(os.path.join(dp, f), folder).replace(os.sep, "/") for f in fn]
    return sorted(out)


def main():
    argv = sys.argv[1:]
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        only = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    if len(argv) != 1:
        sys.exit("usage: run_validations.py <CONTRACT_ID> [--only <script.py>]")
    contract = argv[0].strip().upper()
    contracts = load_json(os.path.join(ROOT, "config", "contracts.json"), {})
    cfg = load_json(os.path.join(ROOT, "config", "validators.json"), {})
    defaults = cfg.get("defaults", {})
    org = contracts.get(contract, {}).get("org", "")
    timeout = int(defaults.get("timeout_minutes", 150)) * 60

    zip_rel = "input/%s/%s.zip" % (contract, contract)
    zip_path = os.path.join(ROOT, zip_rel)
    work = os.path.join(ROOT, "work", contract)
    extracted = os.path.join(work, "extracted")
    input_dir = os.path.join(work, "data")           # parent of <CONTRACT>/*.json
    contract_dir = os.path.join(input_dir, contract)
    cache_dir = os.path.join(work, "cache")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_rel = "reports/%s/run_%s" % (contract, stamp)
    run_dir = os.path.join(ROOT, run_rel)
    os.makedirs(run_dir, exist_ok=True)

    res = {"contract": contract, "org": org, "zip_path": zip_rel, "run_dir": run_rel,
           "started": stamp, "zip_found": False, "extraction": "NOT RUN", "json_count": 0,
           "scripts": [], "email": "NOT REQUESTED", "notes": []}

    def finish():
        res["total"] = len(res["scripts"])
        res["passed"] = sum(s["status"] == "PASS" for s in res["scripts"])
        res["failed"] = res["total"] - res["passed"]
        res["report"] = "GENERATED" if any(s["report_files"] for s in res["scripts"]) else "NOT GENERATED"
        res["overall"] = "PASS" if (res["extraction"] == "PASS" and res["json_count"] > 0
                                    and res["total"] > 0 and res["failed"] == 0) else "FAIL"
        with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)
        with open(os.path.join(ROOT, "work", "results_path.txt"), "w") as f:
            f.write(os.path.join(run_dir, "results.json"))
        print("\nOverall: %s  (%d/%d scripts passed)" % (res["overall"], res["passed"], res["total"]))

    os.makedirs(os.path.join(ROOT, "work"), exist_ok=True)

    # ---- 1-3: ZIP -> JSON
    if not os.path.isfile(zip_path):
        res["extraction"] = "FAIL"
        res["notes"].append("ZIP not found: %s. Upload it via the GitHub UI first (see README)." % zip_rel)
        print("::error::ZIP not found: %s" % zip_rel)
        return finish()
    res["zip_found"] = True
    try:
        shutil.rmtree(work, ignore_errors=True)       # work/ is a temp area only
        os.makedirs(extracted)
        safe_extract(zip_path, extracted)
        files = find_json(extracted)
        stage_json(files, contract_dir)
        res["json_count"] = len(files)
        res["extraction"] = "PASS"
        print("Extracted %s -> %d JSON file(s)" % (zip_rel, len(files)))
        if not files:
            res["notes"].append("ZIP extracted but contains no .json files.")
            print("::error::no JSON files found in %s" % zip_rel)
    except Exception as e:                            # noqa: BLE001 - report any extraction problem
        res["extraction"] = "FAIL"
        res["notes"].append("Extraction failed: %s" % e)
        print("::error::extraction failed: %s" % e)
    os.makedirs(os.path.join(ROOT, "work"), exist_ok=True)
    if res["extraction"] != "PASS" or not res["json_count"]:
        return finish()
    os.makedirs(cache_dir, exist_ok=True)

    # ---- 5-6: run every script, independently
    scripts = discover_scripts(cfg)
    if only:
        scripts = [x for x in scripts if x["name"] == only]
        if not scripts:
            res["notes"].append("Script not found in scripts/: %s" % only)
    print("Discovered %d validation script(s): %s" % (len(scripts), ", ".join(s["name"] for s in scripts)))
    for s in scripts:
        out_dir = os.path.join(run_dir, os.path.splitext(s["name"])[0])
        os.makedirs(out_dir, exist_ok=True)
        fmt = dict(contract=contract, org=org, input_dir=input_dir, contract_dir=contract_dir,
                   output_dir=out_dir, cache_dir=cache_dir)
        cmd = [sys.executable, s["path"]] + [a.format(**fmt) for a in s["args"]]
        env = dict(os.environ, CONTRACT_ID=contract, CONTRACT_ORG=org, INPUT_DIR=input_dir,
                   CONTRACT_DIR=contract_dir, OUTPUT_DIR=out_dir, PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        log_path = os.path.join(out_dir, "run.log")
        print("::group::%s -> %s" % (s["name"], contract))
        print("$ " + " ".join(cmd[1:]))
        t0 = time.time()
        code, note = None, ""
        try:
            with open(log_path, "w", encoding="utf-8") as log:
                p = subprocess.Popen(cmd, cwd=out_dir, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                     errors="replace")
                deadline = t0 + timeout
                for line in p.stdout:
                    sys.stdout.write(line)
                    log.write(line)
                    if time.time() > deadline:
                        p.kill()
                        note = "timed out after %d min" % (timeout // 60)
                        break
                code = p.wait()
        except Exception as e:                        # noqa: BLE001
            note = "could not start: %s" % e
        secs = int(time.time() - t0)
        status = "PASS" if code == 0 and not note else "FAIL"
        print("::endgroup::")
        print("%s  %s  (exit %s, %ds)%s" % (s["name"], status, code, secs, " " + note if note else ""))
        reports = [f for f in list_files(out_dir) if f != "run.log"]
        pats = [p.format(contract=contract) for p in s["dynamic"]]
        found = [f for f in reports if any(fnmatch.fnmatch(os.path.basename(f), p) for p in pats)]
        dyn_status = "N/A" if not pats else ("GENERATED" if found else "MISSING")
        if dyn_status == "MISSING":
            print("::warning::%s: dynamic report not generated (expected %s)" % (s["name"], ", ".join(pats)))
        res["scripts"].append({"name": s["name"], "label": s["label"], "status": status,
                               "exit_code": code, "seconds": secs, "note": note,
                               "folder": os.path.relpath(out_dir, ROOT).replace(os.sep, "/"),
                               "report_files": reports, "attach": s["attach"],
                               "dynamic_status": dyn_status, "dynamic_files": found})
    finish()


if __name__ == "__main__":
    main()
