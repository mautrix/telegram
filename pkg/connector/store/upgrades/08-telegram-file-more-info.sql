-- v8 (compatible with v2+): Add more info to telegram_file
ALTER TABLE telegram_file ADD COLUMN width INTEGER;
ALTER TABLE telegram_file ADD COLUMN height INTEGER;
ALTER TABLE telegram_file ADD COLUMN timestamp BIGINT;
