"""Read-only JSON API over the DuckDB file + the static dashboard. Local tool: no auth, bind to 127.0.0.1."""
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, llm
from .jobs import detect, notify
from .pipeline import run_pipeline

app = FastAPI(title="Fashion Anomaly Detection")
_con = None
_run_lock = threading.Lock()
SEV_RANK = "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"


def q(sql, params=()):
    global _con
    if _con is None:
        _con = db.connect()
    cur = _con.cursor()  # one cursor per request: DuckDB connections are not thread-safe, cursors are
    cur.execute(sql, list(params))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def latest_run():
    run_id = q("SELECT max(run_id) AS r FROM pipeline_runs WHERE status = 'success'")[0]["r"]
    if run_id is None:
        raise HTTPException(404, "No successful pipeline run yet")
    return run_id


@app.get("/api/overview")
def overview():
    r = latest_run()
    kpi = q("""
        SELECT (SELECT rows_out FROM job_runs WHERE run_id = $1 AND job = 'ingest') AS rows_ingested,
               (SELECT count(*) FROM silver_sales) AS sales_rows,
               (SELECT count(DISTINCT (table_name, row_id)) FROM quarantine WHERE run_id = $1) AS rows_quarantined,
               (SELECT count(*) FROM dq_results WHERE run_id = $1) AS rules_total,
               (SELECT count(*) FROM dq_results WHERE run_id = $1 AND violations = 0) AS rules_passed,
               (SELECT count(*) FROM anomalies WHERE run_id = $1) AS anomalies,
               (SELECT count(*) FROM anomalies WHERE run_id = $1 AND severity = 'critical') AS critical,
               (SELECT count(*) FROM anomalies WHERE run_id = $1
                  AND day >= (SELECT max(day) FROM gold_daily) - 14) AS recent,
               (SELECT min(day) FROM gold_daily) AS first_day, (SELECT max(day) FROM gold_daily) AS last_day
        """, [r])[0]
    return {
        "run": q("SELECT run_id, started_at, finished_at, status FROM pipeline_runs WHERE run_id = ?", [r])[0],
        "kpi": kpi,
        "eval": detect.evaluate(_con.cursor(), r),
        "revenue": q("SELECT day, round(sum(revenue)) AS revenue FROM gold_daily GROUP BY 1 ORDER BY 1"),
        "anomaly_days": q(f"""SELECT day, count(*) AS n, min({SEV_RANK}) AS rank FROM anomalies
                              WHERE run_id = ? GROUP BY 1 ORDER BY 1""", [r]),
        "by_severity": q(f"SELECT severity, count(*) AS n FROM anomalies WHERE run_id = ? GROUP BY 1 ORDER BY min({SEV_RANK})", [r]),
        "by_metric": q("SELECT detector, metric, count(*) AS n FROM anomalies WHERE run_id = ? GROUP BY ALL ORDER BY n DESC", [r]),
        "by_store": q("""SELECT s.name, count(*) AS n FROM anomalies a JOIN silver_stores s ON s.store_id = a.entity
                         WHERE run_id = ? GROUP BY 1 ORDER BY n DESC""", [r]),
    }


@app.get("/api/anomalies")
def anomalies():
    return q(f"""
        SELECT a.anomaly_id, a.detector, a.metric, a.entity_type, a.entity, coalesce(s.name, a.entity) AS entity_name,
               a.day, a.sale_id, a.observed, a.expected, a.score, a.severity, a.description
        FROM anomalies a LEFT JOIN silver_stores s ON a.entity_type = 'store' AND s.store_id = a.entity
        WHERE a.run_id = ? ORDER BY a.day DESC, {SEV_RANK}""", [latest_run()])


@app.get("/api/series")
def series(entity: str, metric: str):
    return q("""SELECT day, value, expected, sigma, z FROM gold_scored
                WHERE entity = ? AND metric = ? ORDER BY day""", [entity, metric])


@app.get("/api/quality")
def quality():
    r = latest_run()
    return {
        "rules": q("""SELECT rule, category, severity, table_name, description, total_rows, violations, pass_rate
                      FROM dq_results WHERE run_id = ? ORDER BY category, table_name, rule""", [r]),
        "by_table": q("""SELECT table_name, count(DISTINCT row_id) AS n FROM quarantine
                         WHERE run_id = ? GROUP BY 1 ORDER BY n DESC""", [r]),
        "by_month": q("""SELECT strftime(try_cast(payload->>'order_ts' AS TIMESTAMP), '%Y-%m') AS month, count(*) AS n
                         FROM quarantine WHERE run_id = ? AND table_name = 'bronze_sales'
                           AND try_cast(payload->>'order_ts' AS TIMESTAMP) <= current_date
                         GROUP BY 1 ORDER BY 1""", [r]),
        "sample": q("""SELECT table_name, rule, row_id, payload::VARCHAR AS payload FROM quarantine WHERE run_id = ?
                       QUALIFY row_number() OVER (PARTITION BY rule ORDER BY row_id) <= 2
                       ORDER BY table_name, rule""", [r]),
    }


@app.get("/api/pipeline")
def pipeline():
    runs = q("SELECT run_id, started_at, finished_at, status FROM pipeline_runs ORDER BY run_id DESC LIMIT 15")
    jobs = q("""SELECT run_id, seq, job, duration_ms, status, rows_in, rows_out, error FROM job_runs
                WHERE run_id >= ? ORDER BY run_id, seq""", [runs[-1]["run_id"] if runs else 0])
    for run in runs:
        run["jobs"] = [j for j in jobs if j["run_id"] == run["run_id"]]
    return runs


@app.get("/api/alerts")
def alerts():
    return {
        "policy": {"min_severity": notify.min_severity(), "window_days": notify.RECENT_DAYS,
                   "slack_configured": bool(config.SLACK_WEBHOOK_URL),
                   "llm_model": config.LLM_MODEL if llm.available() else None},
        "items": q("""SELECT run_id, created_at, channel, status, source, model, message
                      FROM notifications ORDER BY created_at DESC LIMIT 20"""),
    }


@app.post("/api/alerts/test")
def test_alert():
    """Check the Slack wiring without waiting for a real anomaly."""
    run_id = latest_run()  # also opens the shared connection
    message = "*Atelier Watch test alert*\nIf you can read this in Slack, the webhook is configured correctly."
    status = notify.send_slack(message)
    notify.record(_con.cursor(), run_id, status, "test", None, message)
    return {"status": status}


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


@app.post("/api/chat")
def chat(req: ChatRequest):
    if req.messages[-1].role != "user":
        raise HTTPException(422, "The last message must come from the user")
    if not llm.available():
        raise HTTPException(503, "No LLM configured: set LLM_BASE_URL and LLM_MODEL in .env")
    run_id = latest_run()  # also opens the shared connection
    facts = notify.build_facts(_con.cursor(), run_id, limit=30)
    reply = llm.answer(facts, [m.model_dump() for m in req.messages[-10:]])  # last 10 turns keep the prompt small
    if reply is None:
        raise HTTPException(502, "The LLM did not answer. Is the model running?")
    return {"reply": reply, "model": config.LLM_MODEL}


@app.post("/api/run")
def trigger_run():
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "A pipeline run is already in progress")
    try:
        q("SELECT 1")  # make sure the shared connection exists
        run_id, status = run_pipeline(_con.cursor())
        return {"run_id": run_id, "status": status}
    finally:
        _run_lock.release()


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
