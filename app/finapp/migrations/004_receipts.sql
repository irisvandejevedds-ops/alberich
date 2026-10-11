CREATE TABLE receipts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT NOT NULL UNIQUE, stored_name TEXT NOT NULL, original_name TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,                                  -- image | pdf
  status TEXT NOT NULL DEFAULT 'queued',               -- queued | processing | parsed | matched | needs_review | failed
  ocr_text TEXT NOT NULL DEFAULT '', ocr_engine TEXT NOT NULL DEFAULT '',
  merchant TEXT NOT NULL DEFAULT '', purchase_date TEXT, total_cents INTEGER, subtotal_cents INTEGER, tax_cents INTEGER,
  verdict TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', accepted INTEGER NOT NULL DEFAULT 0,
  txn_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, processed_at TEXT);
CREATE INDEX ix_receipts_status ON receipts(status);
CREATE TABLE receipt_lines(
  id INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
  pos INTEGER NOT NULL, description TEXT NOT NULL, amount_cents INTEGER NOT NULL);
ALTER TABLE line_items ADD COLUMN item_key TEXT;
