BEGIN;

CREATE TABLE IF NOT EXISTS officials (
    official_id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    jersey_number TEXT,
    nba_stats_person_id INT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_officials_full_name ON officials (LOWER(full_name));

DROP TABLE IF EXISTS game_officials;

CREATE TABLE game_officials (
    game_id TEXT PRIMARY KEY REFERENCES games(game_id),
    crew_chief_id TEXT REFERENCES officials(official_id),
    referee_id TEXT REFERENCES officials(official_id),
    umpire_id TEXT REFERENCES officials(official_id),
    source TEXT NOT NULL,
    announced_at TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ,
    is_late_change BOOLEAN NOT NULL DEFAULT FALSE,
    raw_response_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_game_officials_crew_chief ON game_officials (crew_chief_id);
CREATE INDEX IF NOT EXISTS idx_game_officials_referee ON game_officials (referee_id);
CREATE INDEX IF NOT EXISTS idx_game_officials_umpire ON game_officials (umpire_id);

CREATE TABLE IF NOT EXISTS official_assignment_changes (
    id BIGSERIAL PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    role TEXT NOT NULL,
    announced_official_id TEXT REFERENCES officials(official_id),
    actual_official_id TEXT REFERENCES officials(official_id),
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
