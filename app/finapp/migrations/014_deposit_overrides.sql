CREATE TABLE deposit_overrides(account_id INTEGER NOT NULL, dedupe_key TEXT NOT NULL, PRIMARY KEY(account_id, dedupe_key));   -- deposits the owner said do count as income
