import json
from ftm2.db.core import get_conn

def insert_ticket(amt):
    conn = get_conn()
    with conn:
        conn.execute(
        """
        INSERT OR REPLACE INTO tickets(id,symbol,created_ts,aggr_level,readiness,score,p_up,regime,rv_pr,
          gates_json,plan_json,actions_json,trace_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,?)
        """,
        (
          amt["id"], amt["symbol"], amt["created_ts"], amt["aggr_level"],
          amt["summary"]["readiness"], amt["summary"]["score"], amt["summary"]["p_up"],
          amt["summary"]["regime"], amt["summary"].get("rv_pr"),
          json.dumps(amt["summary"].get("gates"), ensure_ascii=False),
          json.dumps(amt["plan"], ensure_ascii=False),
          json.dumps(amt["actions"], ensure_ascii=False),
          json.dumps(amt["trace"], ensure_ascii=False)
        ))
