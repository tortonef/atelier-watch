"""Summarise the run with the LLM and post it to Slack (dry run when no webhook is configured)."""
from datetime import datetime

import httpx

from .. import config, llm

RECENT_DAYS = 14


def build_facts(con, run_id):
    rows = lambda sql: con.execute(sql, [run_id]).fetchall()
    recent = rows(f"""
        SELECT entity, day, description, severity, detector FROM anomalies
        WHERE run_id = ? AND day >= (SELECT max(day) FROM gold_daily) - {RECENT_DAYS}
        ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, day DESC
        LIMIT 6""")
    failed = rows("""SELECT rule, violations FROM dq_results WHERE run_id = ? AND violations > 0
                     ORDER BY violations DESC""")
    total_rules, = rows("SELECT count(*) FROM dq_results WHERE run_id = ?")[0]
    quarantined, = rows("SELECT count(DISTINCT (table_name, row_id)) FROM quarantine WHERE run_id = ?")[0]
    names = dict(con.execute("SELECT store_id, name FROM silver_stores").fetchall())
    return {
        "run_id": run_id,
        "total_anomalies": rows("SELECT count(*) FROM anomalies WHERE run_id = ?")[0][0],
        "by_severity": dict(rows("SELECT severity, count(*) FROM anomalies WHERE run_id = ? GROUP BY 1")),
        "recent_window_days": RECENT_DAYS,
        "recent_anomalies": [{"entity": names.get(e, e), "day": str(d), "description": desc, "severity": sev,
                              "detector": det} for e, d, desc, sev, det in recent],
        "data_quality": {"rules_total": total_rules, "rules_failed": len(failed), "rows_quarantined": quarantined,
                         "worst_rules": [{"rule": r, "violations": v} for r, v in failed[:3]]},
    }


def run(con, run_id):
    facts = build_facts(con, run_id)
    message, source, model = llm.summarize(facts)
    status = "dry_run"
    if config.SLACK_WEBHOOK_URL:
        try:
            httpx.post(config.SLACK_WEBHOOK_URL, json={"text": message}, timeout=15).raise_for_status()
            status = "sent"
        except httpx.HTTPError as e:
            status = "failed"
            print(f"Slack post failed: {type(e).__name__}")  # never log the webhook URL
    con.execute("INSERT INTO notifications VALUES (?, ?, 'slack', ?, ?, ?, ?)",
                [run_id, datetime.now(), status, source, model, message])
    return facts["total_anomalies"], 1
