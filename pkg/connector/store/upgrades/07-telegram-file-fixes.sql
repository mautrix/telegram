-- v7 (compatible with v2+): Add index and fix values in telegram_file
UPDATE telegram_file SET id=REPLACE(id, '-', '') WHERE id LIKE '%-';
CREATE INDEX telegram_file_mxc_idx ON telegram_file (mxc);
