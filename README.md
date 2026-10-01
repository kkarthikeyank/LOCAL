# MPF Provider Directory Validation (GitHub Actions)

One repo, one validation workflow, one cleanup workflow. Pick a contract, and every script in `scripts/` runs against that contract's ZIP.

```
.github/workflows/manual_validation.yml   Contract dropdown + Send Email Yes/No (default No)
.github/workflows/manual_cleanup.yml      Delete ZIP / Delete JSON (separate, needs typing DELETE)
config/contracts.json                     contract -> org (add new contracts here)
config/validators.json                    per-script CLI args, email attachments (optional per script)
scripts/check_hyLOCAL.py                  dangling FHIR references
scripts/mpf_auditLOCAL.py                 CMS technical guide audit (Word report + findings CSVs)
scripts/validate_maLOCAL.py               Appendix A/B/E field + cross-resource checks
tools/run_validations.py                  unzip -> find JSON -> run all scripts -> results.json
tools/send_email.py                       one email per script, own template (email_templates/)
tools/plan.py, tools/aggregate.py, tools/summary.py   job matrix, combined summary
input/<CONTRACT>/<CONTRACT>.zip           you upload this
reports/<CONTRACT>/                       reports (see "Where reports are")
```

## One-time setup
1. Push this folder's contents to the repo (`kkarthikeyank/LOCAL`, default branch).
2. Settings > Actions > General > Workflow permissions: **Read and write** (needed only by the cleanup workflow).
3. Email secrets (only needed if you ever choose Send Email = Yes): Settings > Secrets and variables > Actions > **New repository secret**:
   `EMAIL_USERNAME`, `EMAIL_PASSWORD` (Gmail: an App Password), `EMAIL_TO` (comma separated).
   Optional *Variables* (not secrets): `SMTP_SERVER` (default `smtp.gmail.com`), `SMTP_PORT` (default `465`; `587` = STARTTLS).

## Upload a ZIP (H1625 shown; identical for H1619, H3124, H9207)
1. Open the repo on GitHub, open `input`, open `H1625`.
2. **Add file > Upload files**, choose your local `H1625.zip` (name must be exactly `H1625.zip`).
3. **Commit changes**.
The ZIP may hold JSON files directly or inside any folder structure; extraction and JSON discovery are automatic.
Browser upload limit is **25 MB per file**; larger ZIPs: `git add input/H1625/H1625.zip && git commit && git push` (limit 100 MB; above that use Git LFS).

## Upload a ZIP larger than 25 MB (e.g. 110 MB) - Release method, all in the browser
The browser cannot upload >25 MB to a folder, and git rejects >100 MB. Use a Release (up to 2 GB per file):
1. Repo page > right side **Releases** > **Draft a new release**.
2. **Choose a tag** > type `input-H1625` > **Create new tag** (use `input-H1619`, `input-H3124`, `input-H9207` for the others).
3. Title: `H1625 input`. Drag your `H1625.zip` into the "Attach binaries" box (file name must be exactly `H1625.zip`), wait for the upload to finish.
4. Tick **Set as a pre-release** and click **Publish release** (not "Save draft").
The validation workflow downloads it automatically when `input/H1625/H1625.zip` is not in the repo (a ZIP in the repo takes priority).
**Replace:** run Cleanup with ZIP=DELETE (removes the release asset), then edit the release and upload the new `H1625.zip`; or edit the release, delete the old file and attach the new one.

## Run validation
Actions > **MPF Manual Validation** > **Run workflow** > Contract ID `H1625`, Script `ALL` (or one script file), Send Email `No` (or `Yes`) > Run.
- Each script runs as its own **parallel job** (own result, own artifact, own email). `ALL` runs every `scripts/*.py`; picking one file runs only that script (e.g. re-run just `validate_maLOCAL.py`). A new script is included in `ALL` automatically; add its file name to the Script dropdown only if you want to run it alone.
- A final **report** job merges everything into one summary with the overall result.
- All scripts run even if one fails; the job summary lists each separately plus Total / Passed / Failed / Overall.
- Reports are uploaded as one artifact per script, **MPF-H1625-<script>-Reports** (only that contract). Download it from the run page.
- A script is **PASS** when it exits 0. Exit 1 means findings: check_hy = dangling references found, mpf_audit = Level 1 fatal findings, validate_ma = failed checks.

### Where reports are
Each run writes `reports/<CONTRACT>/run_<UTC time>/<script>/` (the script's own files, unrenamed, plus `run.log`) and `results.json`. The runner is temporary, so this folder lives in the **artifact**; it is not committed back (report CSVs are 50-100 MB). Nothing is deleted automatically: ZIP, reports and local files are untouched by validation.

### Email
Send Email = Yes sends **one email per script**, each with its own template (`email_templates/<script>.html`, fallback `_default.html`) and its own attachments (Word reports/summary CSVs per `attach` in `config/validators.json`, up to 20 MB; the rest listed as "in the artifact"). Subject: `MPF H1625 Provider Directory Validation Report - <script label>`. Each email has contract, script result, script count, passed/failed, report list, and the Actions run link. A failed send fails the step and shows `Email: FAILED (...)` in the summary. Secrets are only passed as env vars and never printed.

## Cleanup (separate workflow, nothing is deleted by validation)
Actions > **MPF Manual Cleanup** > Contract, **ZIP** KEEP/DELETE, **JSON** KEEP/DELETE, Confirmation `DELETE`.
- ZIP/JSON are independent (any of the four combinations; KEEP+KEEP does nothing).
- Without the exact text `DELETE`, nothing happens.
- ZIP deletes only `input/<contract>/<contract>.zip`. JSON deletes only `.json` files committed under `input/<contract>/`; extracted JSON lives only on the temporary runner and vanishes automatically. Reports, scripts, config and other contracts are never touched.

### Replace an old ZIP
Review the report > run Cleanup with ZIP=DELETE > upload the new `H1625.zip` > run validation again. (Uploading a file with the same name over the old one also works.) Repeatable any number of times.

## Examples
H1625 / H1619 / H3124 / H9207: upload `input/<C>/<C>.zip`, run validation with that Contract ID, optional cleanup the same way. Each contract only ever reads its own ZIP and writes its own reports.

## Add a contract (e.g. H5826)
1. `config/contracts.json`: add `"H5826": {"org": "CHPW"}` (org used by validate_ma).
2. Add `H5826` to the `options:` list in **both** workflow files (GitHub dropdowns are static).
3. Create `input/H5826/` (upload the ZIP there). No script changes needed.

## Add a validation script
Drop `scripts/<anything>.py` (not starting with `_`; helpers should be named `_helper.py`). It is auto-discovered and run for every contract. A script with no entry in `config/validators.json` is run with no arguments and gets env vars: `CONTRACT_ID`, `CONTRACT_ORG`, `INPUT_DIR` (parent of `<CONTRACT>/*.json`), `CONTRACT_DIR` (the JSON files), `OUTPUT_DIR` (also the working directory: write reports there). Exit 0 = PASS, non-zero = FAIL. Optionally add an entry for a label, CLI args (`{contract} {org} {input_dir} {contract_dir} {output_dir} {cache_dir}`), attachments, and a template `email_templates/<script>.html`.

## Changes made to your scripts (logic untouched)
- `check_hyLOCAL.py`: reads JSON from `$INPUT_DIR` when set; exits 1 if dangling references exist.
- `mpf_auditLOCAL.py`: local file pattern `chpw-<C>-*` became `*-<C>-*` (JHP files are `jhp-...`); `--local` accepts contracts not in `INDEX_URLS` (e.g. H1625).
- `validate_maLOCAL.py`: exits 1 if any check failed (previously always 0).

## Troubleshooting
| Symptom | Fix |
|---|---|
| `ZIP not found` | Upload to exactly `input/<C>/<C>.zip` on the default branch |
| `no JSON files found` | ZIP has no `.json`; re-zip the bundle files |
| mpf_audit slow on first step | It downloads the NPPES reference file (large) on each run; this is existing script behaviour |
| `is missing from config/contracts.json` | Add the contract (see above) |
| Email FAILED | Check the 3 secrets; Gmail needs an App Password; check `SMTP_SERVER`/`SMTP_PORT` |
| Cleanup push rejected | Settings > Actions > Workflow permissions = Read and write; branch protection may block bot pushes |
| Script FAIL but reports exist | Open `run.log` in the artifact folder of that script |
