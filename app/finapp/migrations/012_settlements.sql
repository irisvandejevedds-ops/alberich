CREATE TABLE settlements(
  id INTEGER PRIMARY KEY AUTOINCREMENT, paid_on TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  direction TEXT NOT NULL,                      -- from_partner (they paid you back) | to_partner (you paid them)
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, removed_at TEXT);
