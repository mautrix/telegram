-- v2: Add index for ghost username metadata field

CREATE INDEX idx_ghost_username ON ghost ((metadata->>'username'));
