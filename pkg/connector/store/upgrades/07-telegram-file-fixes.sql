-- v7 (compatible with v2+): Add index and fix values in telegram_file
DELETE FROM telegram_file WHERE id LIKE '%-' AND EXISTS(SELECT 1 FROM telegram_file tf2 WHERE tf2.id=REPLACE(telegram_file.id, '-', ''));
UPDATE telegram_file SET id=REPLACE(id, '-', '') WHERE id LIKE '%-';
CREATE INDEX telegram_file_mxc_idx ON telegram_file (mxc);
