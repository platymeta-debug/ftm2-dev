CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY,
  symbol TEXT,
  created_ts REAL,
  aggr_level INTEGER,
  readiness TEXT,
  score REAL,
  p_up REAL,
  regime TEXT,
  rv_pr REAL,
  gates_json TEXT,
  plan_json TEXT,
  actions_json TEXT,
  trace_json TEXT
);
