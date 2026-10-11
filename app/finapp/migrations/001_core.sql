CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE sessions(
  token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, last_seen TEXT NOT NULL,
  expires_at TEXT NOT NULL, user_agent TEXT);

CREATE TABLE push_subscriptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL, auth TEXT NOT NULL, user_agent TEXT, created_at TEXT NOT NULL,
  last_success_at TEXT, last_error TEXT, fail_count INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE notifications_sent(
  id INTEGER PRIMARY KEY AUTOINCREMENT, rule_key TEXT NOT NULL, period_key TEXT NOT NULL,
  sent_at TEXT NOT NULL, ok INTEGER NOT NULL DEFAULT 0, title TEXT, body TEXT,
  UNIQUE(rule_key, period_key));

CREATE TABLE activities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, icon TEXT NOT NULL DEFAULT '',
  phrase TEXT NOT NULL DEFAULT '', estimate_cents INTEGER NOT NULL DEFAULT 0,
  estimate_locked INTEGER NOT NULL DEFAULT 0, weight INTEGER NOT NULL DEFAULT 3,
  credits_per_month INTEGER NOT NULL DEFAULT 0, cadence TEXT NOT NULL DEFAULT '{}',
  sort INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);

CREATE TABLE activity_credit_periods(
  id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
  activity_id INTEGER NOT NULL REFERENCES activities(id),
  allotted INTEGER NOT NULL, estimate_cents INTEGER NOT NULL, unlock_times TEXT NOT NULL,
  UNIQUE(month, activity_id));

CREATE TABLE activity_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id INTEGER NOT NULL REFERENCES activities(id),
  ts TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'live', idempotency_key TEXT UNIQUE,
  note TEXT, undone_at TEXT);
CREATE INDEX ix_logs_ts ON activity_logs(ts);

CREATE TABLE pet_state_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, health REAL NOT NULL,
  mood TEXT NOT NULL, evil INTEGER NOT NULL DEFAULT 0, inputs TEXT);

CREATE TABLE llm_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, purpose TEXT NOT NULL,
  payload_sha256 TEXT, redaction TEXT, bytes INTEGER, cost_cents REAL, model TEXT, ok INTEGER);

CREATE TABLE idempotency_keys(
  key TEXT PRIMARY KEY, endpoint TEXT, response TEXT, created_at TEXT NOT NULL);

CREATE TABLE month_plan(
  month TEXT PRIMARY KEY, income_cents INTEGER NOT NULL DEFAULT 0,
  savings_cents INTEGER NOT NULL DEFAULT 0, buffer_cents INTEGER NOT NULL DEFAULT 0,
  pool_override_cents INTEGER, notes TEXT);
