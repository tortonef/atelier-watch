"""Bronze layer: land every raw file as-is, adding a row id for lineage."""
from .. import config

SOURCES = {
    "bronze_sales": "read_parquet('{raw}/sales.parquet')",
    "bronze_products": "read_csv('{raw}/products.csv')",
    "bronze_stores": "read_json('{raw}/stores.json')",
    "bronze_inventory": "read_csv('{raw}/inventory.csv')",
    "injected_anomalies": "read_csv('{raw}/injected_anomalies.csv')",  # ground truth, used only to score the detectors
}


def run(con, run_id):
    raw = config.RAW_DIR.as_posix()
    rows = 0
    for table, source in SOURCES.items():
        con.execute(f"CREATE OR REPLACE TABLE {table} AS "
                    f"SELECT row_number() OVER () AS _row_id, * FROM {source.format(raw=raw)}")
        if table.startswith("bronze_"):
            rows += con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return rows, rows
