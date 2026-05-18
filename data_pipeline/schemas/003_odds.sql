BEGIN;

CREATE TABLE IF NOT EXISTS prop_lines (
    snapshot_ts TIMESTAMPTZ NOT NULL,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    player_id TEXT NOT NULL REFERENCES players(player_id),
    market TEXT NOT NULL,
    line NUMERIC NOT NULL,
    over_price INT NOT NULL,
    under_price INT NOT NULL,
    book TEXT NOT NULL,
    bookmaker_event_id TEXT,
    bookmaker_key TEXT NOT NULL DEFAULT 'unknown',
    raw_response_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_ts, game_id, player_id, market, book)
);

CREATE TABLE IF NOT EXISTS closing_lines (
    game_id TEXT NOT NULL REFERENCES games(game_id),
    player_id TEXT NOT NULL REFERENCES players(player_id),
    market TEXT NOT NULL,
    line NUMERIC NOT NULL,
    over_price INT NOT NULL,
    under_price INT NOT NULL,
    book TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    minutes_before_tip REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (game_id, player_id, market, book)
);

CREATE INDEX IF NOT EXISTS idx_prop_lines_game_player ON prop_lines(game_id, player_id);
CREATE INDEX IF NOT EXISTS idx_prop_lines_recent ON prop_lines(snapshot_ts DESC);
CREATE INDEX IF NOT EXISTS idx_prop_lines_market_book ON prop_lines(market, book);
CREATE INDEX IF NOT EXISTS idx_closing_lines_game ON closing_lines(game_id);

CREATE TABLE IF NOT EXISTS bookmaker_event_mapping (
    bookmaker_key TEXT NOT NULL,
    bookmaker_event_id TEXT NOT NULL,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    commence_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bookmaker_key, bookmaker_event_id)
);

CREATE TABLE IF NOT EXISTS scraper_run_log (
    id BIGSERIAL PRIMARY KEY,
    bookmaker_key TEXT NOT NULL,
    market TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    events_attempted INT DEFAULT 0,
    events_succeeded INT DEFAULT 0,
    props_inserted INT DEFAULT 0,
    error_message TEXT,
    raw_response_paths TEXT[]
);

CREATE TABLE IF NOT EXISTS team_moneyline_odds (
    snapshot_ts TIMESTAMPTZ NOT NULL,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    team_abbr TEXT NOT NULL,
    moneyline_odds INT NOT NULL,
    book TEXT NOT NULL,
    bookmaker_event_id TEXT,
    raw_response_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_ts, game_id, team_abbr, book)
);

CREATE INDEX IF NOT EXISTS idx_team_moneyline_odds_game_book
    ON team_moneyline_odds(game_id, book);

COMMIT;
