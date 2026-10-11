CREATE TABLE line_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  receipt_id INTEGER, description TEXT NOT NULL DEFAULT '', amount_cents INTEGER NOT NULL,
  category_id INTEGER REFERENCES categories(id),
  my_bps INTEGER NOT NULL DEFAULT 5000,               -- your share in basis points (0 to 10000)
  split_source TEXT NOT NULL DEFAULT 'default',       -- user | rule | learned | default
  confident INTEGER NOT NULL DEFAULT 0,               -- 1 = Alberich is sure enough not to ask
  why TEXT NOT NULL DEFAULT '', reviewed INTEGER NOT NULL DEFAULT 0, decided_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX ix_li_txn ON line_items(txn_id);

CREATE TABLE split_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, merchant_id INTEGER NOT NULL UNIQUE REFERENCES merchants(id) ON DELETE CASCADE,
  my_bps INTEGER NOT NULL, created_at TEXT NOT NULL);

INSERT OR IGNORE INTO categories(name, kind, sort) VALUES
 ('Groceries','variable',1),('Dining','variable',2),('Transport','variable',3),('Household','variable',4),
 ('Fun','variable',5),('Health','variable',6),('Bills','fixed',7),('Shopping','variable',8),('Other','variable',99);
