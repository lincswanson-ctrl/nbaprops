BEGIN;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    abbreviation TEXT NOT NULL UNIQUE,
    conference TEXT,
    division TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE teams ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS abbreviation TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS conference TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS division TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE teams ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team_id TEXT REFERENCES teams(team_id),
    position TEXT,
    height TEXT,
    weight TEXT,
    birthdate DATE,
    draft_year INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE players ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS team_id TEXT REFERENCES teams(team_id);
ALTER TABLE players ADD COLUMN IF NOT EXISTS position TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS height TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS weight TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS birthdate DATE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS draft_year INT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE players ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    date DATE NOT NULL,
    season TEXT NOT NULL,
    home_team_id TEXT NOT NULL REFERENCES teams(team_id),
    away_team_id TEXT NOT NULL REFERENCES teams(team_id),
    home_score INT,
    away_score INT,
    pace NUMERIC,
    total_possessions NUMERIC,
    ot_periods INT DEFAULT 0,
    vegas_total NUMERIC,
    vegas_spread NUMERIC,
    attendance INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE games ADD COLUMN IF NOT EXISTS date DATE;
ALTER TABLE games ADD COLUMN IF NOT EXISTS season TEXT;
ALTER TABLE games ADD COLUMN IF NOT EXISTS home_team_id TEXT REFERENCES teams(team_id);
ALTER TABLE games ADD COLUMN IF NOT EXISTS away_team_id TEXT REFERENCES teams(team_id);
ALTER TABLE games ADD COLUMN IF NOT EXISTS home_score INT;
ALTER TABLE games ADD COLUMN IF NOT EXISTS away_score INT;
ALTER TABLE games ADD COLUMN IF NOT EXISTS pace NUMERIC;
ALTER TABLE games ADD COLUMN IF NOT EXISTS total_possessions NUMERIC;
ALTER TABLE games ADD COLUMN IF NOT EXISTS ot_periods INT DEFAULT 0;
ALTER TABLE games ADD COLUMN IF NOT EXISTS vegas_total NUMERIC;
ALTER TABLE games ADD COLUMN IF NOT EXISTS vegas_spread NUMERIC;
ALTER TABLE games ADD COLUMN IF NOT EXISTS attendance INT;
ALTER TABLE games ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE games ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS player_game_stats (
    player_id TEXT NOT NULL REFERENCES players(player_id),
    game_id TEXT NOT NULL REFERENCES games(game_id),
    team_id TEXT REFERENCES teams(team_id),
    minutes NUMERIC,
    points INT,
    rebounds INT,
    assists INT,
    steals INT,
    blocks INT,
    fga INT,
    fgm INT,
    fg3a INT,
    fg3m INT,
    fta INT,
    ftm INT,
    turnovers INT,
    plus_minus INT,
    usage_rate NUMERIC,
    true_shooting NUMERIC,
    starter BOOLEAN,
    dnp_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_id, game_id)
);

-- Self-heal older bootstrap schemas that were created without the full column set.
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS team_id TEXT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS minutes NUMERIC;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS points INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS rebounds INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS assists INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS steals INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS blocks INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fga INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fgm INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fg3a INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fg3m INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS fta INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS ftm INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS turnovers INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS plus_minus INT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS usage_rate NUMERIC;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS true_shooting NUMERIC;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS starter BOOLEAN;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS dnp_reason TEXT;
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS lineups (
    game_id TEXT NOT NULL REFERENCES games(game_id),
    period INT NOT NULL,
    start_seconds INT NOT NULL,
    end_seconds INT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    player1_id TEXT REFERENCES players(player_id),
    player2_id TEXT REFERENCES players(player_id),
    player3_id TEXT REFERENCES players(player_id),
    player4_id TEXT REFERENCES players(player_id),
    player5_id TEXT REFERENCES players(player_id),
    possessions NUMERIC,
    points_for INT,
    points_against INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (game_id, period, start_seconds, team_id)
);

ALTER TABLE lineups ADD COLUMN IF NOT EXISTS player1_id TEXT REFERENCES players(player_id);
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS player2_id TEXT REFERENCES players(player_id);
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS player3_id TEXT REFERENCES players(player_id);
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS player4_id TEXT REFERENCES players(player_id);
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS player5_id TEXT REFERENCES players(player_id);
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS possessions NUMERIC;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS points_for INT;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS points_against INT;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS injury_status (
    player_id TEXT NOT NULL REFERENCES players(player_id),
    status_date TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    body_part TEXT,
    source TEXT NOT NULL DEFAULT 'unknown',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_id, status_date)
);

CREATE TABLE IF NOT EXISTS load_log (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    natural_key TEXT NOT NULL,
    raw_file_path TEXT NOT NULL,
    raw_file_checksum TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source, endpoint, natural_key)
);

ALTER TABLE load_log ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE load_log ADD COLUMN IF NOT EXISTS endpoint TEXT;
ALTER TABLE load_log ADD COLUMN IF NOT EXISTS natural_key TEXT;
ALTER TABLE load_log ADD COLUMN IF NOT EXISTS raw_file_path TEXT;
ALTER TABLE load_log ADD COLUMN IF NOT EXISTS raw_file_checksum TEXT;
ALTER TABLE load_log ADD COLUMN IF NOT EXISTS loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_player_game_stats_player_date
    ON player_game_stats(player_id, game_id);
CREATE INDEX IF NOT EXISTS idx_player_game_stats_game
    ON player_game_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_games_date
    ON games(date);
CREATE INDEX IF NOT EXISTS idx_games_season
    ON games(season);
CREATE INDEX IF NOT EXISTS idx_injury_status_player_date
    ON injury_status(player_id, status_date DESC);

DROP TRIGGER IF EXISTS trg_teams_updated_at ON teams;
CREATE TRIGGER trg_teams_updated_at
    BEFORE UPDATE ON teams
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_players_updated_at ON players;
CREATE TRIGGER trg_players_updated_at
    BEFORE UPDATE ON players
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_games_updated_at ON games;
CREATE TRIGGER trg_games_updated_at
    BEFORE UPDATE ON games
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_player_game_stats_updated_at ON player_game_stats;
CREATE TRIGGER trg_player_game_stats_updated_at
    BEFORE UPDATE ON player_game_stats
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_lineups_updated_at ON lineups;
CREATE TRIGGER trg_lineups_updated_at
    BEFORE UPDATE ON lineups
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_injury_status_updated_at ON injury_status;
CREATE TRIGGER trg_injury_status_updated_at
    BEFORE UPDATE ON injury_status
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
