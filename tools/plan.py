"""
Build the job matrix: one parallel job per discovered scripts/*.py (or only the selected one).

    python tools/plan.py <ALL|script.py>     # writes matrix=... and expected=... to $GITHUB_OUTPUT
"""
import json
import os
import sys

import run_validations as rv

sel = sys.argv[1] if len(sys.argv) > 1 else "ALL"
cfg = rv.load_json(os.path.join(rv.ROOT, "config", "validators.json"), {})
items = rv.discover_scripts(cfg)
if sel != "ALL":
    items = [i for i in items if i["name"] == sel]
if not items:
    sys.exit("::error::no validation script matches '%s' in scripts/" % sel)
matrix = {"include": [{"script": i["name"], "stem": os.path.splitext(i["name"])[0]} for i in items]}
out = "matrix=%s\nexpected=%s\n" % (json.dumps(matrix), ",".join(i["name"] for i in items))
print(out)
if os.environ.get("GITHUB_OUTPUT"):
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(out)
