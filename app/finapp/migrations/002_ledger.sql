CREATE TABLE accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, institution TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'chequing',              -- chequing | savings | credit_card | line_of_credit
  owner TEXT NOT NULL DEFAULT 'me',                   -- me | partner | joint
  last4 TEXT NOT NULL DEFAULT '', cycle_day INTEGER,  -- day of month the statement closes (blank = month end)
  require_pdf INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  track_from TEXT, created_at TEXT NOT NULL);

CREATE TABLE statement_templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, institution TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
  kind TEXT NOT NULL,                                 -- csv | pdf-text
  spec TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, fixture_name TEXT,
  created_by TEXT NOT NULL DEFAULT 'heuristic', created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE statements(
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER REFERENCES accounts(id),
  template_id INTEGER REFERENCES statement_templates(id), template_version INTEGER,
  kind TEXT NOT NULL, period_start TEXT, period_end TEXT, file_sha256 TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL, stored_name TEXT NOT NULL, opening_cents INTEGER, closing_cents INTEGER,
  opening_derived INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
  -- pending_template | pending_account | parsed | reconciled | needs_review | failed
  note TEXT, txn_count INTEGER NOT NULL DEFAULT 0, received_at TEXT NOT NULL);
CREATE INDEX ix_statements_account ON statements(account_id, period_end);

CREATE TABLE categories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'variable', sort INTEGER NOT NULL DEFAULT 0);

CREATE TABLE merchants(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pattern TEXT NOT NULL UNIQUE,
  default_category_id INTEGER REFERENCES categories(id));

CREATE TABLE transactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id),
  statement_id INTEGER REFERENCES statements(id), posted_date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,                      -- positive = money out of your pocket, negative = money in
  description_raw TEXT NOT NULL, description_norm TEXT NOT NULL, merchant_id INTEGER REFERENCES merchants(id),
  kind TEXT NOT NULL DEFAULT 'spend',                 -- spend | income | transfer | fee | interest | refund | settlement
  transfer_pair_id INTEGER, dedupe_key TEXT NOT NULL, fitid TEXT, source TEXT NOT NULL DEFAULT 'statement',
  created_at TEXT NOT NULL, UNIQUE(account_id, dedupe_key));
CREATE INDEX ix_tx_date ON transactions(posted_date);
CREATE INDEX ix_tx_statement ON transactions(statement_id);
