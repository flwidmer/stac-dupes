ALTER TABLE ingest_runs
ADD COLUMN crawl_mode TEXT NOT NULL DEFAULT 'search'
CHECK (crawl_mode IN ('search', 'collection-items'));
