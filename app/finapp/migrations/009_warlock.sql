CREATE TABLE warlock_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL,              -- me | warlock
  text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'done',    -- running | done | failed
  actions TEXT NOT NULL DEFAULT '[]', applied TEXT NOT NULL DEFAULT '[]', error TEXT, created_at TEXT NOT NULL);
CREATE TABLE warlock_notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'general',
  created_at TEXT NOT NULL, archived_at TEXT);
CREATE TABLE month_adjustments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, kind TEXT NOT NULL,   -- extra_income | extra_expense
  amount_cents INTEGER NOT NULL, label TEXT NOT NULL DEFAULT '', treat TEXT NOT NULL DEFAULT 'exclude',  -- to_debt | to_savings | spent | exclude
  category TEXT, created_at TEXT NOT NULL, removed_at TEXT);
CREATE INDEX ix_adj_month ON month_adjustments(month);
