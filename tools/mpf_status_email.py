"""HTML e-mail body for validate_maLOCAL.py (ported from the maplancopy repo, scripts/email_report.py).
Sending is done by tools/send_email.py; this module only builds the message.

Original notes:

build(ctx)  -> (html, plain_text, subject)
send(...)   -> (ok, error_message)   -- SMTP credentials come ONLY from the
environment (GitHub Secrets): EMAIL_USERNAME, EMAIL_PASSWORD, EMAIL_TO.
Optional secrets: SMTP_SERVER (default smtp.gmail.com), SMTP_PORT (default 587;
465 uses SSL). Gmail needs an App Password as EMAIL_PASSWORD.
Secrets are never printed; error text is scrubbed before it is returned.
"""
import html as _h

GREEN, RED, AMBER, GREY, BLUE = "#1b6b3a", "#b3261e", "#8a5a00", "#5b6575", "#2f5496"
BG = {"COMPLETED": GREEN, "FAILED": RED, "SCRIPT_FAILURE": RED, "DOWNLOAD_FAILURE": RED}
TITLE = {"COMPLETED": "COMPLETED", "FAILED": "FAILED", "SCRIPT_FAILURE": "SCRIPT / RUNTIME FAILURE",
         "DOWNLOAD_FAILURE": "DOWNLOAD FAILURE"}
EXPLAIN = {
    "COMPLETED": "Validation finished and found no failing checks.",
    "FAILED": "Validation finished and found failing checks in the provider directory data (listed below).",
    "SCRIPT_FAILURE": "The validation script did not finish. This is a script/runtime problem, not a data result.",
    "DOWNLOAD_FAILURE": "The provider directory files could not be downloaded, so the data was not validated.",
}


def e(v):
    return _h.escape(str(v if v is not None else ""))


def _fmt(ts):
    return ts.replace("T", " ").replace(".000Z", "Z") if ts else "n/a"


def _failed(ctx):
    return [c for c in (ctx["status"] or {}).get("codes", []) if c["status"] == "FAIL_SEEN"]


def subject_for(ctx):
    c, r = ctx["contract"], ctx["result"]
    if ctx["mode"] == "auto":
        tail = {"COMPLETED": "Provider Directory Updated & Validation Completed",
                "FAILED": "Provider Directory Updated & Validation FAILED",
                "SCRIPT_FAILURE": "Provider Directory Updated & Validation SCRIPT FAILURE",
                "DOWNLOAD_FAILURE": "Provider Directory Updated & DOWNLOAD FAILURE"}[r]
        return f"[MPF AUTO VALIDATION] {c} – {tail}"
    tail = {"COMPLETED": "Validation Report", "FAILED": "Validation Report (FAILED)",
            "SCRIPT_FAILURE": "Validation SCRIPT FAILURE", "DOWNLOAD_FAILURE": "DOWNLOAD FAILURE"}[r]
    return f"[MPF MANUAL VALIDATION] {c} – {tail}"


def _table(head, rows, widths=None):
    th = "".join(f'<th style="text-align:left;padding:7px 9px;background:{BLUE};color:#fff;font-size:12px">{e(h)}</th>' for h in head)
    body = ""
    for i, r in enumerate(rows):
        bg = "#f4f6fa" if i % 2 else "#ffffff"
        body += "<tr>" + "".join(f'<td style="padding:7px 9px;border-bottom:1px solid #d9dee6;background:{bg};font-size:13px;vertical-align:top">{c}</td>' for c in r) + "</tr>"
    return f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;margin:6px 0 14px">{f"<tr>{th}</tr>" + body}</table>'


def _pill(text, color):
    return f'<span style="display:inline-block;padding:2px 9px;border-radius:10px;background:{color};color:#fff;font-size:12px;font-weight:bold">{e(text)}</span>'


def build(ctx):
    c, r, mode = ctx["contract"], ctx["result"], ctx["mode"]
    st = ctx["status"] or {}
    failing = _failed(ctx)
    real_fail = [x for x in failing if not x["warning_only"]]
    warn = [x for x in failing if x["warning_only"]]
    passed = [x for x in (st.get("codes") or []) if x["status"] == "PASS_ONLY"]
    subject = subject_for(ctx)
    color = BG[r]

    intro = ("The CMS Medicare Advantage Plan Finder Provider Directory for contract <b>%s</b> has been updated. "
             "Validation was automatically triggered based on the new provider directory update." % e(c)
             if mode == "auto" else
             "The MPF Provider Directory validation was manually triggered%s." % (f" by {e(ctx['actor'])}" if ctx["actor"] else ""))

    details = [("Contract", e(c)), ("Plan Year", e(ctx["plan_year"])), ("Run Mode", "Automatic" if mode == "auto" else "Manual")]
    if mode == "auto":
        details += [("Previous Update", e(_fmt(ctx["previous"]) if ctx["previous"] else "never processed")), ("New Update", e(_fmt(ctx["current"])))]
    else:
        details += [("Triggered", "GitHub Actions"), ("Directory last updated", e(_fmt(ctx["current"])))]
    details += [("Validation time", f'{ctx["elapsed"] // 60} min {ctx["elapsed"] % 60} s')]
    det_html = _table(["Item", "Value"], [(f"<b>{k}</b>", v) for k, v in details])

    stage_rows = [(e(n), _pill(s, {"PASS": GREEN, "FAIL": RED, "NOT RUN": GREY, "N/A": GREY}.get(s, GREY))) for n, s in ctx["stages"]]
    stage_html = _table(["Stage", "Result"], stage_rows)

    totals = ""
    if st:
        sc = st.get("status_counts", {})
        totals = _table(["Checks passed", "Checks failed", "Codes failing", "Codes passing", "Fatal (Level 1) failing"],
                        [(f'<b style="color:{GREEN}">{st["checks_passed"]}</b>', f'<b style="color:{RED}">{st["checks_failed"]}</b>',
                          f'<b style="color:{RED}">{sc.get("FAIL_SEEN", 0)}</b>', f'<b style="color:{GREEN}">{sc.get("PASS_ONLY", 0)}</b>',
                          e(st.get("fatal_fail")))])

    fail_html = ""
    if failing:
        rows = []
        for x in failing:
            cnt = x["failing_records"] or x["fail_count"]
            rows.append((f'<b>{e(x["code"])}</b>', e(x["name"]), f'<b style="color:{AMBER if x["warning_only"] else RED}">{cnt}</b>'
                         + (" (warning)" if x["warning_only"] else ""),
                         f'<span style="color:{RED}"><b>{e(x["failed_resource_types"])}</b></span>',
                         f'<span style="color:{GREEN}">{e(x["expected"]) or "--"}</span>',
                         f'<span style="color:{RED}">{e(x["actual"]) or "--"}</span>', e(x["example"])))
        fail_html = ("<h3 style=\"margin:18px 0 4px;color:%s\">Failed checks</h3>" % RED +
                     _table(["Code", "Name", "Failing records", "Failed resource type(s)", "Expected", "Actual", "Example"], rows))

    pass_html = ""
    if passed:
        rows = [(e(x["code"]), e(x["name"]), _pill("PASS", GREEN), e(x["passed_resource_types"])) for x in passed]
        pass_html = ("<h3 style=\"margin:18px 0 4px;color:%s\">Passed checks (%d)</h3>" % (GREEN, len(passed)) +
                     _table(["Code", "Name", "Status", "Resource type(s) passed"], rows))

    names = ctx["report_names"]
    rep = "".join(f"<li>{e(n)}</li>" for n in names) or "<li>No report files were produced.</li>"
    rep += f'<li><a href="{e(ctx["run_url"])}">Full reports and logs: GitHub Actions run</a></li>'
    note = f'<p style="color:{AMBER}">{e(ctx["artifact_note"])}</p>' if ctx["artifact_note"] else ""

    others = ""
    if mode == "auto":
        lines = "".join(f"<div>{e(k)} : {'No update – SKIPPED' if v == 'NO CHANGE' else e(v)}</div>" for k, v in sorted(ctx["other_statuses"].items()))
        others = (f'<p style="margin-top:14px"><b>Only {e(c)} was updated, so validation was executed only for {e(c)}.</b></p>'
                  f'<div style="color:{GREY}">{lines}</div>')

    trace = (f'<h3 style="margin:18px 0 4px;color:{RED}">Script output (last lines)</h3>'
             f'<pre style="background:#f4f6fa;padding:10px;font-size:11px;overflow:auto">{e(ctx["log_tail"])}</pre>') if ctx["log_tail"] else ""

    html = f"""<!doctype html><html><body style="margin:0;background:#eef1f6;font-family:Segoe UI,Calibri,Arial,sans-serif;color:#1c2330">
<div style="max-width:860px;margin:0 auto;background:#fff">
<div style="background:{color};color:#fff;padding:18px 22px">
<div style="font-size:12px;letter-spacing:.08em">MPF PROVIDER DIRECTORY VALIDATION &middot; {e(c)}</div>
<div style="font-size:22px;font-weight:bold;margin-top:4px">Validation Status: {e(TITLE[r])}</div></div>
<div style="padding:18px 22px;font-size:14px;line-height:1.5">
<p>Hi Team,</p><p>{intro}</p>
<h3 style="margin:16px 0 4px;color:{BLUE}">Contract details</h3>{det_html}
<h3 style="margin:16px 0 4px;color:{BLUE}">Validation status</h3>
<p><b>Overall Validation Status: <span style="color:{color}">{e(TITLE[r])}</span></b><br>{e(EXPLAIN[r])}</p>
{stage_html}{totals}{fail_html}{trace}{pass_html}
<h3 style="margin:16px 0 4px;color:{BLUE}">Report</h3><ul style="margin-top:4px">{rep}</ul>{note}{others}
<p style="margin-top:20px">Thanks,<br>{'QA team' if mode == 'auto' else 'MPF Provider Directory Automation'}</p>
</div></div></body></html>"""

    lines = [f"Hi Team,", "", f"Contract: {c}   Plan Year: {ctx['plan_year']}   Run Mode: {mode}",
             f"Overall Validation Status: {TITLE[r]}", EXPLAIN[r], ""]
    for x in failing:
        lines.append(f"- {x['code']} {x['name']}: {x['failing_records'] or x['fail_count']} record(s) on {x['failed_resource_types']} "
                     f"(expected: {x['expected'] or '--'} | actual: {x['actual'] or '--'})")
    lines += ["", "Report: " + ", ".join(names), ctx["run_url"], "", "Thanks,", "QA team"]
    return html, "\n".join(lines), subject


# ---- result classification and stage table (from maplancopy scripts/mpf_monitor.py) ----
DOWNLOAD_CODES = {"C4001", "C4002", "C4003"}          # could not reach/download the data
STAGE_CODES = [                                        # console "stage" -> error-code prefixes
    ("Download files", ("C4001", "C4002", "C4003", "C4011", "C4012", "C4013", "C4014", "P1017")),
    ("Parse JSON", ("C4004", "C4015", "C4016", "C4017", "C4018", "N3015")),
    ("Reference validation", ("F5001", "F5002", "F5003", "F5004", "F5005", "F5006", "F5007", "F5008", "F5009")),
    ("FHIR validation", ("A2001", "A2002", "A2003", "A2004", "A2005", "A2006", "A2007", "A2008", "A2009", "A2010", "P1013", "P1014")),
    ("Business validation", ("N3001", "N3002", "N3003", "N3004", "N3005", "N3006", "N3007", "N3008", "N3011", "N3012", "N3013", "N3014",
                              "P1001", "P1004", "P1006", "P1007", "P1008", "P1009", "P1010", "P1011", "P1012", "P1016", "P1018")),
]


def classify(status_json, have_files):
    """COMPLETED / FAILED (data issues) / DOWNLOAD_FAILURE / SCRIPT_FAILURE (no result or no report).
    Warning-only failing codes (e.g. P1004) do not make the run FAILED."""
    if status_json is None or not have_files:
        return "SCRIPT_FAILURE"
    failing = [c for c in status_json["codes"] if c["status"] == "FAIL_SEEN"]
    if any(c["code"] in DOWNLOAD_CODES for c in failing):
        return "DOWNLOAD_FAILURE"
    if any(not c["warning_only"] for c in failing):
        return "FAILED"
    return "COMPLETED"


def stages_from(status_json, script_ok):
    out = []
    codes = (status_json or {}).get("codes", [])
    failing = {c["code"] for c in codes if c["status"] == "FAIL_SEEN" and not c["warning_only"]}
    tested = {c["code"] for c in codes}
    for name, group in STAGE_CODES:
        if not script_ok or not status_json:
            out.append((name, "NOT RUN"))
        elif failing & set(group):
            out.append((name, "FAIL"))
        elif tested & set(group):
            out.append((name, "PASS"))
        else:
            out.append((name, "N/A"))
    return out
