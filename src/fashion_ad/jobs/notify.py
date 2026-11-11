"""Alerting: apply the alert rule, summarise the run with the LLM, post to Slack
(dry run when no webhook is configured)."""
from datetime import datetime

import httpx

from .. import config, llm

RECENT_DAYS = 14
SEVERITIES = ["low", "medium", "high", "critical"]
SEV_RANK = "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"


def min_severity():
    s = config.ALERT_MIN_SEVERITY.lower()
    return s if s in SEVERITIES else "high"


def build_facts(con, run_id, limit=6):
    """Everything the LLM is allowed to know about a run. `limit` caps the recent anomalies listed."""
    rows = lambda sql: con.execute(sql, [run_id]).fetchall()
    recent = rows(f"""
        SELECT entity, day, description, severity, detector FROM anomalies
        WHERE run_id = ? AND day >= (SELECT max(day) FROM gold_daily) - {RECENT_DAYS}
        ORDER BY {SEV_RANK}, day DESC LIMIT {int(limit)}""")
    failed = rows("""SELECT rule, violations FROM dq_results WHERE run_id = ? AND violations > 0
                     ORDER BY violations DESC""")
    total_rules, = rows("SELECT count(*) FROM dq_results WHERE run_id = ?")[0]
    quarantined, = rows("SELECT count(DISTINCT (table_name, row_id)) FROM quarantine WHERE run_id = ?")[0]
    names = dict(con.execute("SELECT store_id, name FROM silver_stores").fetchall())
    first_day, last_day = con.execute("SELECT min(day), max(day) FROM gold_daily").fetchone()
    return {
        "run_id": run_id,
        "data_period": f"{first_day} to {last_day}",
        "total_anomalies": rows("SELECT count(*) FROM anomalies WHERE run_id = ?")[0][0],
        "by_severity": dict(rows("SELECT severity, count(*) FROM anomalies WHERE run_id = ? GROUP BY 1")),
        "by_signal": dict(rows("SELECT metric, count(*) FROM anomalies WHERE run_id = ? GROUP BY 1")),
        "by_store_or_category": {names.get(e, e): n for e, n in rows(
            "SELECT entity, count(*) FROM anomalies WHERE run_id = ? GROUP BY 1 ORDER BY 2 DESC")},
        "recent_window_days": RECENT_DAYS,
        "recent_anomalies": [{"entity": names.get(e, e), "day": str(d), "description": desc, "severity": sev,
                              "detector": det} for e, d, desc, sev, det in recent],
        "data_quality": {"rules_total": total_rules, "rules_failed": len(failed), "rows_quarantined": quarantined,
                         "failed_rules": [{"rule": r, "violations": v} for r, v in failed]},
    }


def send_slack(message):
    """Returns 'sent', 'failed' or 'dry_run' (no webhook configured)."""
    if not config.SLACK_WEBHOOK_URL:
        return "dry_run"
    try:
        httpx.post(config.SLACK_WEBHOOK_URL, json={"text": message}, timeout=15).raise_for_status()
        return "sent"
    except httpx.HTTPError as e:
        print(f"Slack post failed: {type(e).__name__}")  # never log the webhook URL
        return "failed"


def record(con, run_id, status, source, model, message):
    con.execute("INSERT INTO notifications VALUES (?, ?, 'slack', ?, ?, ?, ?)",
                [run_id, datetime.now(), status, source, model, message])


def run(con, run_id):
    # Alert rule: only notify when the recent window holds an anomaly at or above the minimum severity.
    floor = min_severity()
    wanted = SEVERITIES[SEVERITIES.index(floor):]
    qualifying = con.execute(f"""
        SELECT count(*) FROM anomalies WHERE run_id = ? AND severity IN ({', '.join('?' * len(wanted))})
          AND day >= (SELECT max(day) FROM gold_daily) - {RECENT_DAYS}""", [run_id, *wanted]).fetchone()[0]
    if not qualifying:
        record(con, run_id, "suppressed", "rule", None,
               f"No alert sent: no anomaly at or above *{floor}* severity in the last {RECENT_DAYS} days.")
        return 0, 0
    message, source, model = llm.summarize(build_facts(con, run_id))
    record(con, run_id, send_slack(message), source, model, message)
    return qualifying, 1
