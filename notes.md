# Running stac-dupes

## Install and start PostGIS

```shell
poetry install
docker compose up -d --wait db
poetry run stac-dupes init-db
```

`init-db` is safe to run repeatedly. It applies any migrations that have not yet
been recorded in `schema_migrations`.

## Ingest Biomass Level 2A FP_GN__L2A

```shell
poetry run stac-dupes crawl \
  --url https://catalog.maap.eo.esa.int/catalogue/ \
  --collection BiomassLevel2a \
  --query '{"op":"=","args":[{"property":"product:type"},"FP_GN__L2A"]}'
```

The MAAP query currently matches about 9,720 Items. Only STAC metadata is read;
the protected product assets are not downloaded. The processing baseline is read
from `properties.version` and stored in `items.processing_baseline`.

## Resume or re-crawl

The crawl output shows its run ID. If interrupted, resume from its saved STAC
pagination token:

```shell
poetry run stac-dupes crawl --run-id RUN_ID
```

To discard the checkpoint and repeat that run's original query from page one:

```shell
poetry run stac-dupes crawl --run-id RUN_ID --re-crawl
```

## Inspect ingestion

```shell
docker compose exec db psql -U stacdupes -d stacdupes
```

Useful queries inside `psql`:

```sql
SELECT id, status, state, started_at, finished_at
FROM ingest_runs
ORDER BY id DESC;

SELECT collection_id, processing_baseline, count(*)
FROM items
GROUP BY collection_id, processing_baseline
ORDER BY collection_id, processing_baseline;
```

Run the exploratory duplicate checks from the host:

```shell
docker compose exec -T db psql -U stacdupes -d stacdupes < sql/checks.sql
```

These checks only compare items with the same processing baseline. Two missing
baselines compare as equal; a missing and a known baseline do not.

## Stop PostGIS

```shell
docker compose down
```

The named database volume is retained. Use `docker compose down -v` only when the
stored database should be deleted.
