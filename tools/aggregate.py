"""
Merge the per-script results.json files (one per parallel job) into one summary + final pass/fail.

    python tools/aggregate.py <collected_dir> <CONTRACT> <expected,script,names>

Looks for <collected_dir>/*/results.json. A script whose job produced no results (crashed before
running) is shown as FAIL. Appends to $GITHUB_STEP_SUMMARY; exit 1 if overall FAIL.
"""
import glob
import json
import os
import sys

import summary

d, contract, expected = sys.argv[1], sys.argv[2], [e for e in sys.argv[3].split(",") if e]
parts = []
for p in glob.glob(os.path.join(d, "**", "results.json"), recursive=True):
    with open(p, encoding="utf-8") as f:
        parts.append(json.load(f))

by = {}
for r in parts:
    for s in r["scripts"]:
        by[s["name"]] = s
notes = [n for r in parts for n in r["notes"]]
for name in expected:
    if name not in by:
        by[name] = {"name": name, "label": name, "status": "FAIL", "exit_code": "n/a", "seconds": 0,
                    "note": "no result: job failed before the script ran", "report_files": [],
                    "folder": "", "attach": []}
scripts = [by[n] for n in expected if n in by] + [s for n, s in by.items() if n not in expected]
ok = [r for r in parts if r["extraction"] == "PASS"]
emails = sorted({r["email"] for r in parts})
total = len(scripts)
passed = sum(s["status"] == "PASS" for s in scripts)
m = {"contract": contract, "zip_path": "input/%s/%s.zip" % (contract, contract),
     "zip_found": any(r["zip_found"] for r in parts),
     "extraction": "PASS" if ok else "FAIL",
     "json_count": max([r["json_count"] for r in parts] or [0]),
     "scripts": scripts, "total": total, "passed": passed, "failed": total - passed,
     "report": "GENERATED" if any(s["report_files"] for s in scripts) else "NOT GENERATED",
     "email": emails[0] if len(emails) == 1 else ("; ".join(emails) or "NOT REQUESTED"),
     "notes": sorted(set(notes)), "run_dir": ""}
m["overall"] = "PASS" if ok and total and passed == total and m["json_count"] > 0 else "FAIL"
text = summary.render(m)
t = os.environ.get("GITHUB_STEP_SUMMARY")
if t:
    open(t, "a", encoding="utf-8").write(text)
sys.stdout.reconfigure(encoding="utf-8")
print(text)
sys.exit(0 if m["overall"] == "PASS" else 1)
