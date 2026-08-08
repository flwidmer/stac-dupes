# stac-dupes

`stac-dupes` crawls a STAC API CQL2 search into PostGIS. It retains complete STAC
Item JSON so duplicate-detection criteria can be explored in SQL now and versioned
and recomputed later without crawling the source again.

## Setup

Requirements: Python 3.11+, Poetry, Docker, and Docker Compose.

```shell
poetry install
docker compose up -d
poetry run stac-dupes init-db
```

The default database URL is
`postgresql://stacdupes:stacdupes@localhost:5432/stacdupes`. Override it with
`DATABASE_URL` or `--database-url`.

## Crawl

Pass a CQL2 JSON object inline:

```shell
poetry run stac-dupes crawl \
  --url https://example.test/stac \
  --collection sentinel-2 \
  --query '{"op":"=","args":[{"property":"platform"},"sentinel-2a"]}'
```

Or read the filter from a file by prefixing its path with `@`:

```shell
poetry run stac-dupes crawl \
  --url https://example.test/stac \
  --query @filter.json
```

Every processed page and its complete `rel=next` link are committed together. If
a crawl fails, resume from the saved page:

```shell
poetry run stac-dupes crawl --run-id 12
```

To deliberately rerun that run's original search from its first page:

```shell
poetry run stac-dupes crawl --run-id 12 --re-crawl
```

Items are upserted by `(catalog_url, stac_id)`, so both paths are idempotent. A
completed run is a no-op unless `--re-crawl` is supplied.

The normalized `processing_baseline` column is populated from the STAC Item's
`properties.version` value. Different processing baselines are excluded from
duplicate candidates. Two items without a baseline may still be candidates.
The normalized `product_type` column is populated from `properties["product:type"]`;
duplicate candidates must also have matching product types.

For prompted input, run:

```shell
poetry run stac-dupes interactive
```

## Explore duplicates

`sql/checks.sql` contains starting queries for temporal and geometry overlap,
exact geometry/time matches, and reused IDs across catalogs. Run them manually,
for example:

```shell
docker compose exec -T db psql -U stacdupes -d stacdupes < sql/checks.sql
```

These queries are deliberately not part of the CLI yet. See `PLAN.md` for the
future versioned-criteria and migration design.

`notes.md` contains the current MAAP Biomass ingestion and inspection commands.
