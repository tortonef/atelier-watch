"""Silver: typed rows that passed the quality gate. Gold: daily store x category aggregates."""


def run(con, run_id):
    run_id = int(run_id)
    clean = lambda t: f"_row_id NOT IN (SELECT row_id FROM quarantine WHERE run_id = {run_id} AND table_name = '{t}')"

    con.execute(f"""
        CREATE OR REPLACE TABLE silver_products AS
        SELECT product_id, name, category, season, list_price::DOUBLE AS list_price, cost::DOUBLE AS cost
        FROM bronze_products WHERE {clean('bronze_products')};

        CREATE OR REPLACE TABLE silver_stores AS
        SELECT store_id, name, city, country, channel FROM bronze_stores WHERE {clean('bronze_stores')};

        CREATE OR REPLACE TABLE silver_inventory AS
        SELECT snapshot_date::DATE AS snapshot_date, store_id, product_id, stock_qty::INTEGER AS stock_qty
        FROM bronze_inventory WHERE {clean('bronze_inventory')};

        CREATE OR REPLACE TABLE silver_sales AS
        SELECT s.sale_id, s.order_ts, s.order_ts::DATE AS day, s.store_id, s.product_id, p.category, s.size,
               s.quantity::INTEGER AS quantity, s.unit_price::DOUBLE AS unit_price, p.list_price,
               s.discount_pct::DOUBLE AS discount_pct, s.returned, s.quantity * s.unit_price AS amount
        FROM bronze_sales s
        JOIN silver_products p USING (product_id)
        JOIN silver_stores st USING (store_id)
        WHERE s.{clean('bronze_sales')};

        -- sums (not ratios) so the detector can roll up to store or category level
        CREATE OR REPLACE TABLE gold_daily AS
        SELECT store_id, category, day, sum(amount) AS revenue, sum(quantity) AS units, count(*) AS orders,
               sum(discount_pct) AS discount_sum, count(*) FILTER (WHERE returned) AS returns
        FROM silver_sales GROUP BY ALL;
    """)
    rows_in = con.execute("SELECT count(*) FROM bronze_sales").fetchone()[0]
    rows_out = con.execute("SELECT count(*) FROM silver_sales").fetchone()[0]
    return rows_in, rows_out
