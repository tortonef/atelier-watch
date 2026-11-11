"""End-to-end check on a small synthetic dataset: the rules catch the seeded dirty rows,
nothing dirty reaches silver, and the detectors recover the seeded anomalies."""
import pytest

from fashion_ad import config, db
from fashion_ad.generate import generate
from fashion_ad.jobs import detect
from fashion_ad.pipeline import run_pipeline


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fashion")
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "RAW_DIR", tmp / "raw")
    mp.setattr(config, "LLM_BASE_URL", "")       # no network in tests: template + dry run
    mp.setattr(config, "SLACK_WEBHOOK_URL", "")
    generate(days=240, seed=7)
    con = db.connect(tmp / "test.duckdb")
    run_id, status = run_pipeline(con)
    yield con, run_id, status
    con.close()
    mp.undo()


def test_all_jobs_succeed(run):
    con, run_id, status = run
    assert status == "success"
    assert con.execute("SELECT count(*) FROM job_runs WHERE run_id = ? AND status = 'success'", [run_id]).fetchone()[0] == 5


def test_rules_catch_seeded_dirty_rows(run):
    con, run_id, _ = run
    v = dict(con.execute("SELECT rule, violations FROM dq_results WHERE run_id = ?", [run_id]).fetchall())
    assert v["sales_pk_unique"] == 50
    assert v["sales_store_not_null"] == 30
    assert v["sales_product_fk"] == 40
    assert v["sales_quantity_positive"] == 25
    assert v["sales_price_positive"] == 20
    assert v["sales_discount_range"] == 15
    assert v["sales_size_valid"] == 30
    assert v["sales_date_not_future"] == 10
    assert v["products_pk_unique"] == 2
    assert v["inventory_stock_non_negative"] == 20
    assert v["sales_store_fk"] == v["stores_pk_unique"] == v["sales_freshness"] == 0


def test_silver_is_clean(run):
    con, _, _ = run
    dirty, rows, ids = con.execute("""
        SELECT count(*) FILTER (WHERE quantity <= 0 OR unit_price <= 0 OR discount_pct NOT BETWEEN 0 AND 100
                                   OR store_id IS NULL OR day > current_date),
               count(*), count(DISTINCT sale_id) FROM silver_sales""").fetchone()
    assert dirty == 0 and rows == ids > 0


def test_detectors_recover_seeded_anomalies(run):
    con, run_id, _ = run
    score = detect.evaluate(con, run_id)
    assert score["recall"] >= 0.9, score
    assert score["precision"] >= 0.6, score


def test_alert_falls_back_to_template_and_dry_run(run):
    con, run_id, _ = run
    status, source, message = con.execute(
        "SELECT status, source, message FROM notifications WHERE run_id = ?", [run_id]).fetchone()
    assert (status, source) == ("dry_run", "template")
    assert "anomalies detected" in message
