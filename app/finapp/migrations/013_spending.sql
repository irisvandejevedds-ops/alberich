ALTER TABLE activities ADD COLUMN auto_credits INTEGER NOT NULL DEFAULT 1;     -- 1 = credits follow the category's spending
CREATE TABLE category_mods(category_id INTEGER PRIMARY KEY REFERENCES categories(id) ON DELETE CASCADE, pct REAL NOT NULL);   -- +/- percent change wanted for one category
