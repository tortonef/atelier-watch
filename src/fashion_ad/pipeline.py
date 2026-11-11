"""Job runner and CLI:  python -m fashion_ad.pipeline generate | run | serve"""
import argparse
import time
from datetime import datetime

from . import db
from .jobs import detect, ingest, notify, quality, transform

# Each job is fn(con, run_id) -> (rows_in, rows_out). Order matters; a failure skips the rest.
JOBS = [("ingest", ingest.run), ("quality", quality.run), ("transform", transform.run),
        ("detect", detect.run), ("notify", notify.run)]


def run_pipeline(con=None):
    con = con or db.connect()
    run_id = con.execute("SELECT coalesce(max(run_id), 0) + 1 FROM pipeline_runs").fetchone()[0]
    con.execute("INSERT INTO pipeline_runs VALUES (?, ?, NULL, 'running')", [run_id, datetime.now()])
    status = "success"
    for seq, (name, fn) in enumerate(JOBS, 1):
        started, t0 = datetime.now(), time.perf_counter()
        rows_in = rows_out = error = None
        if status == "failed":
            job_status = "skipped"
        else:
            try:
                rows_in, rows_out = fn(con, run_id)
                job_status = "success"
            except Exception as e:  # recorded and surfaced in the UI; the run stops here
                job_status = status = "failed"
                error = f"{type(e).__name__}: {e}"
        ms = int((time.perf_counter() - t0) * 1000)
        con.execute("INSERT INTO job_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [run_id, seq, name, started, ms, job_status, rows_in, rows_out, error])
        print(f"  {name:<10} {job_status:<8} {ms:>6} ms  in={rows_in} out={rows_out} {error or ''}")
    con.execute("UPDATE pipeline_runs SET finished_at = ?, status = ? WHERE run_id = ?", [datetime.now(), status, run_id])
    return run_id, status


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["generate", "run", "serve"])
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    if args.command == "generate":
        from .generate import generate
        print(generate())
    elif args.command == "run":
        con = db.connect()
        run_id, status = run_pipeline(con)
        print(f"run #{run_id}: {status}", detect.evaluate(con, run_id) if status == "success" else "")
        raise SystemExit(status != "success")
    else:
        import uvicorn
        uvicorn.run("fashion_ad.api:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
