-- Spec 06: carry under prices downstream and record whether market probability is vig-free.
-- Additive only; safe to re-run.
BEGIN;

ALTER TABLE odds ADD COLUMN IF NOT EXISTS under_price INT;

ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_under_book TEXT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_under_price INT;
ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS vig_removed BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
