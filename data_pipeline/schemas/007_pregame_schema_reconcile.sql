-- Reconcile pregame tables with the schema orchestration/pregame.py used to build at runtime
-- (_ensure_pregame_tables). Brings a database that has 005 + 006 applied to exactly that end
-- state, so the pipeline no longer issues DDL itself (CLAUDE.md: schema changes only in
-- reviewed migrations).
--
-- Idempotent: every statement is IF [NOT] EXISTS. On a database where the pipeline already
-- ran, this is a no-op.
--
-- REVIEW NOTE (Lincoln): the DROP CONSTRAINT statements below remove the composite primary
-- keys from 005. They are replaced by the unique indexes at the end, which allow a NULL
-- player_id (a primary key cannot). This mirrors what the runtime code has been doing on
-- every pregame run; no data is dropped.
BEGIN;

-- Player-level columns (005 tables were game-level).
ALTER TABLE odds ADD COLUMN IF NOT EXISTS player_id TEXT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS player_id TEXT;
ALTER TABLE features ADD COLUMN IF NOT EXISTS player_id TEXT;
ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS player_id TEXT;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS player_id TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS player_id TEXT;

-- Market source / bookkeeping columns.
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_book TEXT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_price INT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS odds_asof TIMESTAMPTZ;
ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS market_book TEXT;
ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS market_price INT;
ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS odds_asof TIMESTAMPTZ;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS market_book TEXT;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS market_price INT;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS decision TEXT;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS book TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS price INT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS grade TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS grade_reason TEXT;

-- Spec 06 columns (also in 006; repeated so 007 alone reaches the full end state).
ALTER TABLE odds ADD COLUMN IF NOT EXISTS under_price INT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_under_book TEXT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_under_price INT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS vig_removed BOOLEAN NOT NULL DEFAULT FALSE;

-- Composite primary keys -> unique indexes that include player_id (see review note).
ALTER TABLE odds_normalized DROP CONSTRAINT IF EXISTS odds_normalized_pkey;
ALTER TABLE model_scores DROP CONSTRAINT IF EXISTS model_scores_pkey;
ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_pkey;
ALTER TABLE features DROP CONSTRAINT IF EXISTS features_pkey;

CREATE INDEX IF NOT EXISTS idx_picks_date_status ON picks(game_date, status);
CREATE INDEX IF NOT EXISTS idx_edges_game_market ON edges(game_id, market);
CREATE INDEX IF NOT EXISTS idx_odds_game_market_book ON odds(game_id, market, book);
CREATE UNIQUE INDEX IF NOT EXISTS ux_odds_normalized_game_player_market
    ON odds_normalized(game_id, player_id, market);
CREATE UNIQUE INDEX IF NOT EXISTS ux_model_scores_game_player_market_scored
    ON model_scores(game_id, player_id, market, scored_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_edges_game_player_market_side_scored
    ON edges(game_id, player_id, market, side, scored_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_features_game_player_feature_generated
    ON features(game_id, player_id, feature_name, generated_at);

COMMIT;
