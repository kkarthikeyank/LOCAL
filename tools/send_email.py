"""
Send ONE email per validation script (separate template + attachments for each).

    python tools/send_email.py <results.json>

Secrets come from env only (never printed):  EMAIL_USERNAME, EMAIL_PASSWORD, EMAIL_TO
Optional env:  SMTP_SERVER (default smtp.gmail.com), SMTP_PORT (default 465, SSL; 587 = STARTTLS),
               RUN_URL (link to this Actions run)

Template for script X.py = email_templates/X.html, else email_templates/_default.html.
Placeholders: {{contract}} {{label}} {{script}} {{status}} {{exit_code}} {{duration}} {{overall}}
{{total}} {{passed}} {{failed}} {{json_count}} {{reports}} {{attachments}} {{skipped}}
{{log_tail}} {{run_url}} {{zip_path}} {{html_report}} (check_hy: inline report tables).

Attachments: the script's 'attach' globs (config/validators.json) in priority order, until the size
budget is used; anything left out is listed as "in the artifact". Updates results.json["email"].
Exit 1 if any email failed.
"""
import fnmatch
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_html_report import build_html_report  # noqa: E402  (ported from maplan)
import mpf_status_email  # noqa: E402  (ported from maplancopy; used for validate_maLOCAL.py)
import audit_email  # noqa: E402  (ported from MApalnE2E; used for mpf_auditLOCAL.py)
from types import SimpleNamespace  # noqa: E402
TPL = os.path.join(ROOT, "email_templates")


def template_for(script):
    for name in (os.path.splitext(script)[0] + ".html", "_default.html"):
        p = os.path.join(TPL, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    raise FileNotFoundError("no email template for %s and no _default.html" % script)


def fill(tpl, vals):
    for k, v in vals.items():
        tpl = tpl.replace("{{%s}}" % k, v)
    return tpl


def pick_attachments(s, budget):
    base = os.path.join(ROOT, s["folder"])
    chosen, skipped, used = [], [], 0
    seen = set()
    for pat in s["attach"] or ["*"]:
        for rel in s["report_files"]:
            if rel in seen or not fnmatch.fnmatch(os.path.basename(rel), pat):
                continue
            seen.add(rel)
            size = os.path.getsize(os.path.join(base, rel))
            if used + size <= budget:
                chosen.append(rel)
                used += size
            else:
                skipped.append("%s (%.1f MB)" % (rel, size / 1048576))
    skipped += ["%s (not matched by attach list)" % r for r in s["report_files"] if r not in seen]
    return chosen, skipped


def log_tail(s, n=25):
    try:
        with open(os.path.join(ROOT, s["folder"], "run.log"), encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "(no log)"


def ma_prepare(r, s, budget_mb=14):
    """validate_maLOCAL.py attachments, as maplancopy: <C>_contract_summary_<date>.docx and
    <C>_validation_report_<date>.xlsx (zipped if too big; 14 MB cap), copied next to the originals."""
    import shutil
    import zipfile
    from datetime import datetime, timezone
    c, base = r["contract"], os.path.join(ROOT, s["folder"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    docx_src = os.path.join(base, "contract_summary_%s.docx" % c)
    xlsx_src = next((os.path.join(base, f) for f in s["report_files"]
                     if f.startswith("validation_report_") and f.endswith(".xlsx")), None)
    have = os.path.exists(docx_src) and xlsx_src is not None
    files, note = [], ""
    if have:
        docx_dst = "%s_contract_summary_%s.docx" % (c, today)
        xlsx_dst = "%s_validation_report_%s.xlsx" % (c, today)
        shutil.copy(docx_src, os.path.join(base, docx_dst))
        shutil.copy(xlsx_src, os.path.join(base, xlsx_dst))
        files.append(docx_dst)
        room = int(budget_mb * 1048576) - os.path.getsize(os.path.join(base, docx_dst))
        if os.path.getsize(os.path.join(base, xlsx_dst)) <= room:
            files.append(xlsx_dst)
        else:
            zip_dst = xlsx_dst[:-5] + ".zip"
            with zipfile.ZipFile(os.path.join(base, zip_dst), "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                z.write(os.path.join(base, xlsx_dst), xlsx_dst)
            if os.path.getsize(os.path.join(base, zip_dst)) <= room:
                files.append(zip_dst)
            else:
                note = "The Excel validation report is too large to attach; download it from the workflow run."
    return files, have, note


def status_email(r, s, files, run_url, have_files=True, note=""):
    """validate_maLOCAL.py: maplancopy status email built from run_status_<C>.json."""
    st = None
    try:
        with open(os.path.join(ROOT, s["folder"], "run_status_%s.json" % r["contract"]), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        pass
    result = mpf_status_email.classify(st, have_files)
    stages = mpf_status_email.stages_from(st, st is not None)
    stages.append(("Generate report", "PASS" if have_files else "FAIL"))
    ctx = {
        "contract": r["contract"], "result": result, "mode": "manual", "status": st,
        "plan_year": os.environ.get("PLAN_YEAR", "2027"), "actor": os.environ.get("GITHUB_ACTOR", ""),
        "previous": "", "current": "", "elapsed": int(s["seconds"]), "stages": stages,
        "report_names": [os.path.basename(f) for f in files], "run_url": run_url or "n/a",
        "artifact_note": note, "other_statuses": {},
        "log_tail": log_tail(s, 15) if result == "SCRIPT_FAILURE" else "",
    }
    html, text, _ = mpf_status_email.build(ctx)
    with open(os.path.join(ROOT, s["folder"], "%s_validation_%s.html" % (r["contract"], __import__("datetime").date.today())),
              "w", encoding="utf-8") as f:
        f.write(html)
    return html, text


def audit_status_email(r, s, files, run_url):
    """mpf_auditLOCAL.py: MApalnE2E-style findings email built from findings_<C>.csv."""
    folder = os.path.join(ROOT, s["folder"])
    csv_path = os.path.join(folder, "findings_%s.csv" % r["contract"])
    # exit 0 = no fatal findings, 1 = fatal findings (both are real results); anything else = crash
    crashed = s["exit_code"] not in (0, 1) or bool(s["note"])
    kind = "automation-crash" if crashed else "manual-run"
    docx = next((f for f in files if f.lower().endswith(".docx")), "")
    args = SimpleNamespace(
        kind=kind, contract=r["contract"], plan_year=os.environ.get("PLAN_YEAR", "2027"),
        run_mode="manual", previous_last_updated="", new_last_updated="",
        exit_code=str(s["exit_code"]), report_path=docx, csv_path=csv_path,
        error_detail=(s["note"] + "\n" if s["note"] else "") + log_tail(s, 40) if crashed else "")
    summary = None if crashed else audit_email.summarize_findings_csv(csv_path)
    _, text, html = audit_email.build_message(args, summary)
    return html, text


def li(items):
    return "<ul>%s</ul>" % "".join("<li>%s</li>" % escape(i) for i in items) if items else "<p>none</p>"


def send(msg, user, pw, host, port):
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=120) as sm:
            sm.login(user, pw)
            sm.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=120) as sm:
            sm.starttls(context=ssl.create_default_context())
            sm.login(user, pw)
            sm.send_message(msg)


def main():
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    with open(os.path.join(ROOT, "config", "validators.json"), encoding="utf-8") as f:
        budget = int(json.load(f).get("defaults", {}).get("max_attach_mb", 20)) * 1048576

    user = os.environ.get("EMAIL_USERNAME", "").strip()
    pw = os.environ.get("EMAIL_PASSWORD", "")
    to = [a.strip() for a in os.environ.get("EMAIL_TO", "").replace(";", ",").split(",") if a.strip()]
    host = os.environ.get("SMTP_SERVER", "").strip() or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT", "").strip() or 465)
    run_url = os.environ.get("RUN_URL", "")

    def save(status):
        r["email"] = status
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2)

    if not (user and pw and to):
        save("FAILED (EMAIL_USERNAME / EMAIL_PASSWORD / EMAIL_TO secret missing)")
        print("::error::Email requested but EMAIL_USERNAME, EMAIL_PASSWORD and/or EMAIL_TO secrets are not set.")
        sys.exit(1)
    if not r["scripts"]:
        save("FAILED (no validation ran, nothing to send)")
        print("::error::No validation results to email.")
        sys.exit(1)

    sent, failed = 0, []
    for s in r["scripts"]:
        try:
            files, skipped = pick_attachments(s, budget)
            vals = {
                "contract": r["contract"], "label": s["label"], "script": s["name"],
                "status": s["status"], "exit_code": str(s["exit_code"]), "duration": "%ds" % s["seconds"],
                "overall": r["overall"], "total": str(r["total"]), "passed": str(r["passed"]),
                "failed": str(r["failed"]), "json_count": str(r["json_count"]),
                "reports": li(s["report_files"]), "attachments": li(files), "skipped": li(skipped),
                "log_tail": escape(log_tail(s)), "run_url": escape(run_url or "n/a"),
                "zip_path": escape(r["zip_path"]),
            }
            plain = None
            if s["name"] == "validate_maLOCAL.py":     # maplancopy-style status email
                files, have, note = ma_prepare(r, s)
                html, plain = status_email(r, s, files, run_url, have, note)
            elif s["name"] == "mpf_auditLOCAL.py":     # MApalnE2E-style findings email
                html, plain = audit_status_email(r, s, files, run_url)
            else:
                tpl = template_for(s["name"])
                if "{{html_report}}" in tpl:      # full report tables inline, like maplan
                    vals["html_report"] = build_html_report(reports_dir=os.path.join(ROOT, s["folder"]))
                html = fill(tpl, vals)
            msg = EmailMessage()
            msg["Subject"] = "MPF %s Provider Directory Validation Report - %s" % (r["contract"], s["label"])
            msg["From"] = user
            msg["To"] = ", ".join(to)
            msg.set_content("MPF %s - %s: %s\nOverall: %s (%s passed / %s failed of %s)\nRun: %s\n"
                            "Open this email in an HTML-capable client for the full report summary."
                            % (r["contract"], s["label"], s["status"], r["overall"], r["passed"],
                               r["failed"], r["total"], run_url or "n/a") if plain is None else plain)
            msg.add_alternative(html, subtype="html")
            for rel in files:
                with open(os.path.join(ROOT, s["folder"], rel), "rb") as fh:
                    msg.add_attachment(fh.read(), maintype="application", subtype="octet-stream",
                                       filename=os.path.basename(rel))
            send(msg, user, pw, host, port)
            sent += 1
            print("Email sent: %s (%d attachment(s))" % (s["name"], len(files)))
        except Exception as e:                        # noqa: BLE001
            # Do not echo str(e) blindly if it could contain credentials; smtplib errors don't, but be safe.
            msg_txt = str(e).replace(pw, "***") if pw else str(e)
            failed.append(s["name"])
            print("::error::Email FAILED for %s: %s" % (s["name"], msg_txt))
    if failed:
        save("FAILED (%d sent, %d failed: %s)" % (sent, len(failed), ", ".join(failed)))
        sys.exit(1)
    save("SENT (%d email(s) to %d recipient(s))" % (sent, len(to)))


if __name__ == "__main__":
    main()
