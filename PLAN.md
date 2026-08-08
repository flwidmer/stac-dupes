# STAC duplicate checker plan

## Initial scope

- Run PostGIS with Docker Compose.
- Initialize the database through numbered SQL migrations.
- Search a STAC API with a CQL2 JSON filter through `pystac-client`.
- Store complete Item JSON, normalized geometry, and sensing times in PostGIS.
- Track ingestion runs and checkpoint the complete STAC pagination link after each page.
- Resume an interrupted run or deliberately re-crawl it using idempotent item upserts.
- Provide regular and interactive Click commands with an item progress bar.
- Keep duplicate checks as exploratory SQL initially.

## Data identity

Items have an internal `BIGSERIAL` primary key and are uniquely identified externally by
`(catalog_url, stac_id)`. Re-crawling updates the stored representation and the run that
last observed the item rather than creating another row.

The full STAC Item document is retained in `JSONB`. Geometry and sensing-time columns are
derived indexes for analysis; they are not the sole copy of source data.

The processing baseline is normalized from `properties.version` while preserving its text
representation (for example, `02`). It does not change item identity. Duplicate candidate
queries require baselines to be equal using `IS NOT DISTINCT FROM`, so different baselines
are excluded while two items with unknown baselines can still be compared.

Product type is normalized from `properties["product:type"]` into `product_type`. Duplicate
candidate queries require product types to match using the same null-aware comparison.

## Migrations and future detection criteria

`stac-dupes init-db` applies packaged, numbered migrations and records each migration in
`schema_migrations`. The initial migration only creates ingestion tables. Future detection
support should be additive, for example:

- `criteria`: immutable, named, versioned JSONB definitions.
- `detection_runs`: a specific criteria version applied to a population of stored items.
- `duplicate_pairs` or `duplicate_groups`: results referencing internal item primary keys
  and the detection run that produced them.

Changing a criterion creates a new version instead of modifying an old definition. Detection
results can therefore be recreated from the stored criteria version and raw Item JSONB without
re-crawling a catalog. Frequently used JSON properties may later be backfilled into indexed
columns through additive migrations, while Item JSONB remains the source of truth.

## Implementation sequence

1. Scaffold Poetry package and Compose service.
2. Add migration runner and initial schema.
3. Add page-oriented crawler, checkpoints, and idempotent upserts.
4. Add Click commands and interactive prompting.
5. Add documentation, exploratory queries, and tests.
