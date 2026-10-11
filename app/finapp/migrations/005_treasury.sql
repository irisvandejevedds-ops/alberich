CREATE TABLE budgets(category_id INTEGER PRIMARY KEY REFERENCES categories(id) ON DELETE CASCADE, amount_cents INTEGER NOT NULL DEFAULT 0);
ALTER TABLE activities ADD COLUMN category_id INTEGER REFERENCES categories(id);
CREATE TABLE month_close(
  month TEXT PRIMARY KEY, closed_at TEXT NOT NULL, spent_cents INTEGER NOT NULL, budget_cents INTEGER NOT NULL,
  ratio REAL, verdict TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE TABLE estimate_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE, month TEXT NOT NULL,
  old_cents INTEGER NOT NULL, new_cents INTEGER NOT NULL, spent_cents INTEGER NOT NULL, predicted_cents INTEGER NOT NULL, created_at TEXT NOT NULL);
