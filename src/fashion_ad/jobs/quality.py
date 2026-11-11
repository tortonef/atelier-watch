"""Evaluate every rule on bronze, record the result, quarantine rows that break `error` rules."""
from ..rules import RULES


def run(con, run_id):
    for r in RULES:
        total = con.execute(f"SELECT count(*) FROM {r.table}").fetchone()[0]
        if r.predicate:
            violations = con.execute(f"SELECT count(*) FROM {r.table} t WHERE {r.predicate}").fetchone()[0]
            if r.severity == "error" and violations:
                con.execute(f"INSERT INTO quarantine SELECT ?, ?, ?, t._row_id, to_json(t) "
                            f"FROM {r.table} t WHERE {r.predicate}", [run_id, r.table, r.name])
            pass_rate = 1 - violations / total if total else 1.0
        else:
            violations = con.execute(r.scalar).fetchone()[0] or 0
            pass_rate = 0.0 if violations else 1.0
        con.execute("INSERT INTO dq_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [run_id, r.name, r.category, r.severity, r.table, r.description, total, violations, pass_rate])
    checked = con.execute("SELECT sum(n) FROM (SELECT DISTINCT table_name, total_rows n FROM dq_results WHERE run_id = ?)",
                          [run_id]).fetchone()[0]
    quarantined = con.execute("SELECT count(DISTINCT (table_name, row_id)) FROM quarantine WHERE run_id = ?",
                              [run_id]).fetchone()[0]
    return checked, checked - quarantined
