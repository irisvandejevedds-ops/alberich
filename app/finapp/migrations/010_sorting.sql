INSERT OR IGNORE INTO categories(name, kind, sort) VALUES ('Snacks','variable',3),('Pets','variable',6);
CREATE TABLE merchant_sorts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
  rows TEXT NOT NULL DEFAULT '[]', error TEXT, applied_at TEXT);
