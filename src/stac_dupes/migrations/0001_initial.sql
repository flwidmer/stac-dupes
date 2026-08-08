CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE ingest_runs (
    id BIGSERIAL PRIMARY KEY,
    catalog_url TEXT NOT NULL,
    cql2_filter JSONB NOT NULL,
    collections JSONB NOT NULL DEFAULT '[]'::jsonb,
    page_size INTEGER NOT NULL CHECK (page_size > 0),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE items (
    id BIGSERIAL PRIMARY KEY,
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_runs(id),
    catalog_url TEXT NOT NULL,
    stac_id TEXT NOT NULL,
    collection_id TEXT,
    geometry geometry(Geometry, 4326),
    sensing_start TIMESTAMPTZ,
    sensing_end TIMESTAMPTZ,
    item JSONB NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalog_url, stac_id),
    CHECK (
        sensing_start IS NULL
        OR sensing_end IS NULL
        OR sensing_start <= sensing_end
    )
);

CREATE INDEX items_geometry_idx ON items USING gist (geometry);
CREATE INDEX items_sensing_time_idx
    ON items USING gist (tstzrange(sensing_start, sensing_end, '[]'));
CREATE INDEX items_ingest_run_id_idx ON items (ingest_run_id);
