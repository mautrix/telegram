-- v3 (compatible with v2+): Migrate disappearing timer to standard column

-- only: postgres
UPDATE portal SET disappear_type='after_send', disappear_timer=(metadata->>'messages_ttl')::BIGINT * 1000000000;
-- only: sqlite
UPDATE portal SET disappear_type='after_send', disappear_timer=CAST(metadata->>'$.messages_ttl' AS INTEGER) * 1_000_000_000;

-- The above migration sets disappear type/timer for all portals, so clear out the ones that don't have a TTL
UPDATE portal SET disappear_type=NULL, disappear_timer=NULL WHERE disappear_timer=0;

-- Finally, reset the timer set ca
-- only: postgres
UPDATE portal SET cap_state=jsonb_delete(cap_state, 'flags') WHERE disappear_timer IS NOT NULL;
-- only: sqlite
UPDATE portal SET cap_state=json_remove(cap_state, '$.flags') WHERE disappear_timer IS NOT NULL;
