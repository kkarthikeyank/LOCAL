"""
Render the GitHub Actions job summary from results.json (all values dynamic).

    python tools/summary.py <results.json>        # appends to $GITHUB_STEP_SUMMARY (or prints)
"""
import json
import os
import sys


def render(r):
    ic = {"PASS": "✅", "FAIL": "❌"}
    L = ["# MPF PROVIDER DIRECTORY VALIDATION", "",
         "| | |", "|---|---|",
         "| **Contract ID** | %s |" % r["contract"],
         "| **Input ZIP** | `%s`%s |" % (r["zip_path"], "" if r["zip_found"] else " (NOT FOUND)"),
         "| **ZIP Extraction** | %s %s |" % (ic.get(r["extraction"], "➖"), r["extraction"]),
         "| **JSON Files Found** | %s |" % r["json_count"]]
    for i, s in enumerate(r["scripts"], 1):
        L.append("| **Validation %d** | %s %s |" % (i, ic[s["status"]], s["status"]))
    L += ["| **Total Validations** | %d |" % r["total"],
          "| **Passed** | %d |" % r["passed"],
          "| **Failed** | %d |" % r["failed"],
          "| **Report** | %s |" % r["report"],
          "| **Email** | %s |" % r["email"],
          "| **Overall Result** | %s **%s** |" % (ic[r["overall"]], r["overall"]), ""]
    if r["scripts"]:
        L += ["## VALIDATION RESULTS", "", "| # | Script | Result | Exit | Time | Report files |",
              "|---|---|---|---|---|---|"]
        for i, s in enumerate(r["scripts"], 1):
            L.append("| %d | `%s` | %s %s | %s | %ds | %d%s |" % (
                i, s["name"], ic[s["status"]], s["status"], s["exit_code"], s["seconds"],
                len(s["report_files"]), " — " + s["note"] if s["note"] else ""))
        L.append("")
    for n in r["notes"]:
        L.append("> ⚠️ %s" % n)
    L += ["", "Reports: `%s` (artifact **MPF-%s-Validation-Reports**)" % (r["run_dir"], r["contract"]),
          "", "_Nothing was deleted. Use the **MPF Manual Cleanup** workflow to delete the ZIP or JSON._"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        text = render(json.load(f))
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
