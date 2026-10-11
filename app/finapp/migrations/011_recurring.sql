CREATE TABLE recurring_costs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  every TEXT NOT NULL DEFAULT 'month',                       -- week | 2weeks | month | year
  category_id INTEGER REFERENCES categories(id), my_bps INTEGER NOT NULL DEFAULT 10000,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
