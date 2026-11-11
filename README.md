# Atelier Watch — Fashion Anomaly Detection

An end-to-end anomaly detection platform for fashion retail data: a custom ETL with a
quality gate, two complementary detectors, an LLM that turns the findings into a Slack
alert and answers questions about the run, and a dashboard to explore everything.

![Overview](docs/screenshots/overview.png)

## What it does

```mermaid
flowchart LR
    subgraph raw [Landing zone]
        A[products.csv]
        B[stores.json]
        C[sales.parquet]
        D[inventory.csv]
    end
    raw --> I[ingest] --> BR[(bronze)]
    BR --> Q[quality<br/>20 rules]
    Q -->|error rows| QU[(quarantine)]
    Q --> T[transform] --> S[(silver)] --> G[(gold_daily)]
    G --> DT[detect<br/>robust z-score]
    S --> DT2[detect<br/>Isolation Forest]
    DT --> AN[(anomalies)]
    DT2 --> AN
    AN --> N[notify<br/>LLM summary] --> SL[Slack]
    AN --> UI[FastAPI + dashboard]
```

| Layer | Tech | Notes |
|---|---|---|
| Storage | DuckDB (single file) | reads CSV, JSON and Parquet natively; the ETL is mostly SQL |
| ETL | 5 Python jobs run in order | every job logs status, duration and rows in/out to `job_runs`; a failure skips the rest |
| Data quality | 20 declarative SQL rules | `error` rows are quarantined with their payload, `warn` rows pass through |
| Detection | robust z-score + Isolation Forest | scored against seeded ground truth |
| Alerting | alert rule → any OpenAI-compatible LLM (Groq, Ollama) → Slack webhook | template fallback and dry run when not configured |
| Assistant | same LLM, grounded on the latest run | answers from KPIs, recent anomalies and failed rules only |
| UI | FastAPI + vanilla JS + ECharts | no build step |

## Quick start

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m fashion_ad.pipeline generate   # synthetic data -> data/raw
.venv/Scripts/python -m fashion_ad.pipeline run        # run the 5 jobs
.venv/Scripts/python -m fashion_ad.pipeline serve      # dashboard on http://127.0.0.1:8000
```

The pipeline can also be triggered from the dashboard ("Run pipeline"). While the server is
running it holds the DuckDB file, so use the button rather than the CLI.

Optional: copy `.env.example` to `.env` to enable the LLM (Groq key, or a local Ollama model)
and the Slack webhook. With nothing configured the alert is written from a template and
stored as a dry run, so the whole demo works offline.

## The data

`generate` builds about 18 months of sales for 10 stores and 300 products (~630k sale
lines) with weekly and yearly seasonality, then deliberately damages it in two ways:

- **Dirty rows** for the quality gate: duplicate keys, missing or unknown foreign keys,
  negative quantities, impossible discounts, invalid sizes, future dates, negative stock.
- **Business anomalies**, labelled in `injected_anomalies.csv` and used only to score the
  detectors: store outages, category demand spikes, misconfigured promotions, return
  surges, price typos, bulk orders, off-hours sales.

## Data integrity and quality

Rules live in [`rules.py`](src/fashion_ad/rules.py) as plain SQL predicates, evaluated on
the bronze layer. Integrity rules cover primary-key uniqueness, NOT NULL and foreign keys;
quality rules cover value ranges, allowed sets, future dates, freshness and daily volume.

![Data quality](docs/screenshots/quality.png)

## Detection

| Detector | Input | How | Catches |
|---|---|---|---|
| Robust z-score | daily revenue, units, average discount and return rate per store and per category | expected value = median of the same weekday over the previous 10 weeks; spread = MAD of the relative residuals over the same window; flag when \|z\| ≥ 4.5 | outages, demand spikes, promo errors, return surges |
| Isolation Forest | every sale | features: deviation of the unit price from list price × discount, quantity, hours outside opening time | price typos, bulk orders, off-hours sales |

On the seeded dataset the detectors recover **100% of the injected anomalies at 75%
precision**. Matching is strict (same sale, or same entity and day), so most "false
positives" are real side effects of a seeded event — for example a store-wide promo error
also lifts the average discount of each category that day.

![Anomalies](docs/screenshots/anomalies.png)

## Pipeline and alerts

![Pipeline](docs/screenshots/pipeline.png)

The `notify` job applies the **alert rule** first: it only notifies when the last 14 days
hold at least one anomaly at or above `ALERT_MIN_SEVERITY` (default `high`); otherwise the
run is recorded as "no alert". When the rule fires, the most severe recent anomalies plus
the data quality summary go to the LLM and the result is posted to Slack. The Alerts view
shows the rule, every message, and a "Send test alert" button to check the webhook.

![Alerts](docs/screenshots/alerts.png)

## Assistant

The Assistant view is a chat grounded on the latest run. Each question is sent to the LLM
together with a compact JSON context (totals by severity, signal and entity, the top recent
anomalies, the failed quality rules), and the model is told to answer from that context
only. It needs an LLM configured in `.env`; there is no template fallback for chat.

## Tests

```bash
.venv/Scripts/python -m pytest
```

One end-to-end test module: the rules catch exactly the seeded dirty rows, nothing dirty
reaches silver, the detectors meet a minimum recall and precision, the alert falls back
to the template without network access, and the alert rule suppresses low-severity runs.

## Limitations

- The data is synthetic and the anomalies are large by design; thresholds would need tuning on real data.
- Each run reprocesses the full history (no incremental loads) and there is no scheduler.
- The dashboard has no authentication and binds to localhost only.
- The assistant only knows what is in its context (latest run, top 30 recent anomalies); it cannot query the database.
