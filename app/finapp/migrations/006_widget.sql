CREATE TABLE api_tokens(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT);
