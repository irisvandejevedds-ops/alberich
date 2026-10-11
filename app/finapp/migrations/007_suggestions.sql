CREATE TABLE budget_suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, params TEXT NOT NULL, payload TEXT NOT NULL,
  result TEXT NOT NULL, applied_at TEXT);
