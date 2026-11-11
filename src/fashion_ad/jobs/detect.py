"""Two detectors writing to `anomalies`:
1. time series: robust z-score (median / MAD) against the same weekday of the previous weeks
2. transactions: Isolation Forest on per-sale features"""
import numpy as np
from sklearn.ensemble import IsolationForest

Z_THRESHOLD = 4.5
SIGMA_FLOOR = 0.05      # relative sigma never below 5%
WINDOW_WEEKS = 10
IF_THRESHOLD = -0.55    # IsolationForest score_samples: lower = more anomalous
OPEN_HOUR, CLOSE_HOUR = 9, 20

METRIC_LABEL = {"revenue": "Revenue", "units": "Units sold", "avg_discount": "Average discount", "return_rate": "Return rate"}


def _severity_z(z):
    z = abs(z)
    return "critical" if z >= 10 else "high" if z >= 7 else "medium" if z >= 5.5 else "low"


def _fmt(metric, v):
    if metric == "revenue":
        return f"€{v:,.0f}"
    if metric == "units":
        return f"{v:,.0f}"
    return f"{v:.1f}%" if metric == "avg_discount" else f"{v * 100:.1f}%"


def score_series(con):
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_scored AS
        WITH rollup AS (
            SELECT 'store' AS entity_type, store_id AS entity, day, sum(revenue) AS revenue, sum(units)::DOUBLE AS units,
                   sum(discount_sum) / sum(orders) AS avg_discount, sum(returns) / sum(orders) AS return_rate
            FROM gold_daily GROUP BY ALL
            UNION ALL
            SELECT 'category', category, day, sum(revenue), sum(units)::DOUBLE,
                   sum(discount_sum) / sum(orders), sum(returns) / sum(orders)
            FROM gold_daily GROUP BY ALL
        ),
        series AS (UNPIVOT rollup ON revenue, units, avg_discount, return_rate INTO NAME metric VALUE value),
        base AS (
            SELECT *, median(value) OVER w AS expected, count(*) OVER w AS n
            FROM series
            WINDOW w AS (PARTITION BY entity, metric, dayofweek(day) ORDER BY day
                         ROWS BETWEEN {WINDOW_WEEKS} PRECEDING AND 1 PRECEDING)
        ),
        resid AS (SELECT *, value / nullif(expected, 0) - 1 AS rel FROM base),
        scored AS (
            -- spread of the relative residuals over the whole window (all weekdays): ~70 points, a stable estimate
            SELECT *, greatest(1.4826 * mad(rel) OVER (PARTITION BY entity, metric ORDER BY day
                              ROWS BETWEEN {WINDOW_WEEKS * 7} PRECEDING AND 1 PRECEDING), {SIGMA_FLOOR}) AS sigma_rel
            FROM resid
        )
        SELECT entity_type, entity, metric, day, value, expected, sigma_rel * abs(expected) AS sigma,
               CASE WHEN n >= 6 THEN rel / sigma_rel END AS z
        FROM scored
    """)
    rows = con.execute(f"""SELECT entity_type, entity, metric, day, value, expected, z
                           FROM gold_scored WHERE abs(z) >= {Z_THRESHOLD}""").fetchall()
    out = []
    for etype, entity, metric, day, value, expected, z in rows:
        pct = (value - expected) / expected * 100 if expected else 0
        desc = f"{METRIC_LABEL[metric]} {pct:+.0f}% vs expected ({_fmt(metric, value)} vs {_fmt(metric, expected)})"
        out.append(("zscore", metric, etype, entity, day, None, value, expected, float(z), _severity_z(z), desc))
    return out


def score_transactions(con):
    df = con.execute("""SELECT sale_id, store_id, day, unit_price, list_price, discount_pct, quantity,
                               hour(order_ts) AS hour, amount FROM silver_sales
                        ORDER BY sale_id""").df()  # fixed order: the forest's subsampling must be reproducible
    # Features are ~constant for a normal sale, so anything odd is easy to isolate.
    df["price_dev"] = (df.unit_price / df.list_price - (1 - df.discount_pct / 100)).round(2)
    df["off_hours"] = np.maximum(0, np.maximum(OPEN_HOUR - df.hour, df.hour - CLOSE_HOUR))
    X = df[["price_dev", "quantity", "off_hours"]].to_numpy(dtype=float)
    # max_samples must be large: with the default 256 a tree almost never sees an anomaly, so it never splits on it
    model = IsolationForest(n_estimators=100, max_samples=min(len(X), 50_000), random_state=0, n_jobs=-1).fit(X)
    uniq, inverse = np.unique(X, axis=0, return_inverse=True)  # few distinct rows: score each once
    df["score"] = model.score_samples(uniq)[inverse.ravel()]
    out = []
    for r in df[df.score <= IF_THRESHOLD].itertuples():
        if abs(r.price_dev) > 0.2:
            metric, desc = "unit_price", f"Unit price €{r.unit_price:,.2f} vs list €{r.list_price:,.2f} at {r.discount_pct:.0f}% discount"
        elif r.quantity >= 10:
            metric, desc = "quantity", f"Bulk sale of {r.quantity} units (€{r.amount:,.0f})"
        elif r.off_hours > 0:
            metric, desc = "hour", f"Sale registered at {r.hour:02d}:00, outside opening hours"
        else:
            metric, desc = "combination", "Unusual combination of price, quantity and time"
        sev = "critical" if r.score <= -0.85 else "high" if r.score <= -0.80 else "medium" if r.score <= -0.74 else "low"
        expected = r.list_price * (1 - r.discount_pct / 100) * (1 if metric == "unit_price" else min(r.quantity, 3))
        observed = r.unit_price if metric == "unit_price" else r.amount
        out.append(("isolation_forest", metric, "store", r.store_id, r.day.date(), int(r.sale_id),
                    float(observed), float(expected), float(r.score), sev, desc))
    return out, len(df)


def run(con, run_id):
    found = score_series(con)
    txn, n_rows = score_transactions(con)
    found += txn
    con.executemany("INSERT INTO anomalies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(run_id, i + 1, *row) for i, row in enumerate(found)])
    return n_rows, len(found)


def evaluate(con, run_id):
    """Precision / recall against the injected ground truth (same sale, or same entity and day)."""
    match = """(a.sale_id IS NOT NULL AND a.sale_id = l.sale_id)
               OR (a.sale_id IS NULL AND a.entity = l.entity AND a.day = l.day)
               OR (l.sale_id IS NULL AND a.entity = l.entity AND a.day = l.day)"""
    tp_a, n_a = con.execute(f"""SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM injected_anomalies l WHERE {match})),
                                       count(*) FROM anomalies a WHERE run_id = ?""", [run_id]).fetchone()
    tp_l, n_l = con.execute(f"""SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM anomalies a WHERE a.run_id = ? AND ({match}))),
                                       count(*) FROM injected_anomalies l""", [run_id]).fetchone()
    return {"precision": tp_a / n_a if n_a else 0.0, "recall": tp_l / n_l if n_l else 0.0,
            "detected": n_a, "injected": n_l}
