BEGIN;

CREATE TABLE IF NOT EXISTS odds (
    id BIGSERIAL PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    market TEXT NOT NULL,
    book TEXT NOT NULL,
    line NUMERIC NOT NULL,
    price INT NOT NULL,
    odds_timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS odds_normalized (
    game_id TEXT NOT NULL REFERENCES games(game_id),
    market TEXT NOT NULL,
    consensus_line NUMERIC NOT NULL,
    consensus_price NUMERIC NOT NULL,
    fair_prob NUMERIC NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (game_id, market)
);

CREATE TABLE IF NOT EXISTS features (
    game_id TEXT NOT NULL REFERENCES games(game_id),
    feature_name TEXT NOT NULL,
    value NUMERIC NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (game_id, feature_name, generated_at)
);

CREATE TABLE IF NOT EXISTS model_scores (
    game_id TEXT NOT NULL REFERENCES games(game_id),
    market TEXT NOT NULL,
    model_prob NUMERIC NOT NULL,
    model_line NUMERIC,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_version TEXT NOT NULL,
    PRIMARY KEY (game_id, market, scored_at)
);

CREATE TABLE IF NOT EXISTS edges (
    game_id TEXT NOT NULL REFERENCES games(game_id),
    market TEXT NOT NULL,
    side TEXT NOT NULL,
    model_prob NUMERIC NOT NULL,
    market_prob NUMERIC NOT NULL,
    edge_pct NUMERIC NOT NULL,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_version TEXT NOT NULL,
    PRIMARY KEY (game_id, market, side, scored_at)
);

CREATE TABLE IF NOT EXISTS picks (
    pick_id BIGSERIAL PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    game_date DATE NOT NULL,
    market TEXT NOT NULL,
    side TEXT NOT NULL,
    line NUMERIC,
    edge_pct NUMERIC NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    model_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_picks_date_status ON picks(game_date, status);
CREATE INDEX IF NOT EXISTS idx_edges_game_market ON edges(game_id, market);
CREATE INDEX IF NOT EXISTS idx_odds_game_market_book ON odds(game_id, market, book);

COMMIT;
