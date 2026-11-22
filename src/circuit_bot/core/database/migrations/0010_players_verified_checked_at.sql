-- The last time the startup sweep asked Bloxlink about this player.  NULL
-- means the sweep hasn't, so it asks; a marked player is skipped on later
-- startups.
ALTER TABLE players ADD COLUMN verified_checked_at timestamptz;
