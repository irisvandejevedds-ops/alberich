-- Syncing with phones: pouches and their logs get a stable id (uuid), a change counter (rev) that rises on every insert or edit so a phone can ask
-- "what changed since rev N", and an updated_at used to settle two edits of the same row (the later one wins).
ALTER TABLE activities ADD COLUMN uuid TEXT;
ALTER TABLE activities ADD COLUMN rev INTEGER NOT NULL DEFAULT 0;
ALTER TABLE activities ADD COLUMN updated_at TEXT;
ALTER TABLE activity_logs ADD COLUMN uuid TEXT;
ALTER TABLE activity_logs ADD COLUMN rev INTEGER NOT NULL DEFAULT 0;
ALTER TABLE activity_logs ADD COLUMN updated_at TEXT;
UPDATE activities SET
  uuid = lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-'||substr('89ab',abs(random())%4+1,1)||substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6))),
  rev = id, updated_at = created_at;
UPDATE activity_logs SET
  uuid = lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-'||substr('89ab',abs(random())%4+1,1)||substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6))),
  rev = id, updated_at = COALESCE(undone_at, ts);
CREATE UNIQUE INDEX ix_activities_uuid ON activities(uuid);
CREATE UNIQUE INDEX ix_logs_uuid ON activity_logs(uuid);
CREATE INDEX ix_activities_rev ON activities(rev);
CREATE INDEX ix_logs_rev ON activity_logs(rev);
CREATE TRIGGER activities_ai AFTER INSERT ON activities BEGIN
  UPDATE activities SET
    uuid = COALESCE(NEW.uuid, lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-'||substr('89ab',abs(random())%4+1,1)||substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6)))),
    rev = (SELECT COALESCE(MAX(rev), 0) + 1 FROM activities),
    updated_at = COALESCE(NEW.updated_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
  WHERE id = NEW.id;
END;
CREATE TRIGGER activities_au AFTER UPDATE ON activities WHEN NEW.rev = OLD.rev BEGIN
  UPDATE activities SET
    rev = (SELECT MAX(rev) + 1 FROM activities),
    updated_at = CASE WHEN NEW.updated_at IS OLD.updated_at THEN strftime('%Y-%m-%dT%H:%M:%SZ', 'now') ELSE NEW.updated_at END
  WHERE id = NEW.id;
END;
CREATE TRIGGER logs_ai AFTER INSERT ON activity_logs BEGIN
  UPDATE activity_logs SET
    uuid = COALESCE(NEW.uuid, lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-'||substr('89ab',abs(random())%4+1,1)||substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6)))),
    rev = (SELECT COALESCE(MAX(rev), 0) + 1 FROM activity_logs),
    updated_at = COALESCE(NEW.updated_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
  WHERE id = NEW.id;
END;
CREATE TRIGGER logs_au AFTER UPDATE ON activity_logs WHEN NEW.rev = OLD.rev BEGIN
  UPDATE activity_logs SET
    rev = (SELECT MAX(rev) + 1 FROM activity_logs),
    updated_at = CASE WHEN NEW.updated_at IS OLD.updated_at THEN strftime('%Y-%m-%dT%H:%M:%SZ', 'now') ELSE NEW.updated_at END
  WHERE id = NEW.id;
END;
ALTER TABLE api_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'widget';
