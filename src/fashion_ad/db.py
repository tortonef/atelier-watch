import duckdb

from . import config

# Metadata tables persist across runs; bronze/silver/gold are rebuilt by the jobs.
DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id INTEGER PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR);
CREATE TABLE IF NOT EXISTS job_runs (
    run_id INTEGER, seq INTEGER, job VARCHAR, started_at TIMESTAMP, duration_ms INTEGER,
    status VARCHAR, rows_in BIGINT, rows_out BIGINT, error VARCHAR);
CREATE TABLE IF NOT EXISTS dq_results (
    run_id INTEGER, rule VARCHAR, category VARCHAR, severity VARCHAR, table_name VARCHAR,
    description VARCHAR, total_rows BIGINT, violations BIGINT, pass_rate DOUBLE);
CREATE TABLE IF NOT EXISTS quarantine (
    run_id INTEGER, table_name VARCHAR, rule VARCHAR, row_id BIGINT, payload JSON);
CREATE TABLE IF NOT EXISTS anomalies (
    run_id INTEGER, anomaly_id INTEGER, detector VARCHAR, metric VARCHAR,
    entity_type VARCHAR, entity VARCHAR, day DATE, sale_id BIGINT,
    observed DOUBLE, expected DOUBLE, score DOUBLE, severity VARCHAR, description VARCHAR);
CREATE TABLE IF NOT EXISTS notifications (
    run_id INTEGER, created_at TIMESTAMP, channel VARCHAR, status VARCHAR,
    source VARCHAR, model VARCHAR, message VARCHAR);
"""


def connect(path=None):
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(DDL)
    return con
