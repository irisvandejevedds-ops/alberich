-- the fixed reference points: your pay and your rent, each with a start date so a raise or a new split adds a line instead of rewriting history
CREATE TABLE anchors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,                 -- salary | rent
  amount_cents INTEGER NOT NULL, every TEXT NOT NULL DEFAULT 'month',       -- week | 2weeks | semimonthly | month (salary)
  my_pct REAL NOT NULL DEFAULT 100,                                         -- your share of the rent
  start_date TEXT NOT NULL, match TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
-- what the owner decided about deposits that might be the partner paying back, keyed so it survives reading a statement again
CREATE TABLE repayment_marks(
  account_id INTEGER NOT NULL, dedupe_key TEXT NOT NULL,
  counted_cents INTEGER,                                                    -- null = the whole deposit counts against what the partner owes
  status TEXT NOT NULL,                                                     -- confirmed | rejected
  PRIMARY KEY(account_id, dedupe_key));
