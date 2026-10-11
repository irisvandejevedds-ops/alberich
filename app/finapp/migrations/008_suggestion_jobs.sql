ALTER TABLE budget_suggestions ADD COLUMN status TEXT NOT NULL DEFAULT 'done';
ALTER TABLE budget_suggestions ADD COLUMN error TEXT;
