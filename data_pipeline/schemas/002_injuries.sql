BEGIN;

ALTER TABLE injury_status ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE injury_status ADD COLUMN IF NOT EXISTS body_part TEXT;
ALTER TABLE injury_status ADD COLUMN IF NOT EXISTS raw_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE injury_status ADD COLUMN IF NOT EXISTS game_id TEXT;

ALTER TABLE injury_status DROP CONSTRAINT IF EXISTS injury_status_pkey;
ALTER TABLE injury_status ADD PRIMARY KEY (player_id, status_date, source);

CREATE INDEX IF NOT EXISTS idx_injury_status_recent ON injury_status(status_date DESC);
CREATE INDEX IF NOT EXISTS idx_injury_status_player_recent ON injury_status(player_id, status_date DESC);

CREATE TABLE IF NOT EXISTS player_name_aliases (
    alias TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    source TEXT NOT NULL,
    confirmed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS unresolved_player_names (
    raw_name TEXT NOT NULL,
    source TEXT NOT NULL,
    team_hint TEXT,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    occurrences INT DEFAULT 1,
    suggested_player_id TEXT,
    suggested_confidence REAL,
    PRIMARY KEY (raw_name, source)
);

COMMIT;
