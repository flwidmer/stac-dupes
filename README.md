# stac-dupes

`stac-dupes` crawls a STAC API CQL2 search into PostGIS. It retains complete STAC
Item JSON so duplicate-detection criteria can be explored in SQL now and versioned
and recomputed later without crawling the source again.

## Setup

Requirements: Python 3.11+, Make, curl, Docker, and Docker Compose.

```shell
make install
docker compose up -d
poetry run stac-dupes init-db
```

The default database URL is
`postgresql://stacdupes:stacdupes@localhost:5432/stacdupes`. Override it with
`DATABASE_URL` or `--database-url`.

## Development

Run linting and verify formatting with Ruff:

```shell
make lint
poetry run ruff format --check .
```

Apply automatic fixes and formatting with:

```shell
poetry run ruff check --fix .
poetry run ruff format .
```

## Crawl

To crawl every item in a collection without a CQL2 filter:

```shell
poetry run stac-dupes crawl \
  --url https://example.test/stac \
  --collection sentinel-2
```

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
a crawl fails, resume from the saved page. Transient rejections of a freshly
issued pagination token are retried automatically:

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

For a notebook-driven crawl, open `notebooks/crawl.ipynb`. It contains editable
catalog and CQL2 settings and can start, resume, or re-crawl an ingestion run:

```shell
poetry run jupyter lab notebooks/crawl.ipynb
```

## Explore duplicates

`notebooks/duplicate_checks.ipynb` contains starting queries for temporal and
geometry overlap, exact geometry/time matches, and reused IDs across catalogs.
With PostGIS running and initialized, start JupyterLab from the host:

```shell
poetry run jupyter lab
```

The notebook uses `DATABASE_URL`, or the default connection from Setup, and
limits query results to 100 rows for interactive exploration. To execute it
headlessly and write the result outside the repository:

```shell
poetry run jupyter nbconvert \
  --to notebook \
  --execute notebooks/duplicate_checks.ipynb \
  --output-dir /tmp \
  --output duplicate_checks.executed.ipynb
```

Notebook outputs are not committed. Clear them before committing changes:

```shell
poetry run jupyter nbconvert \
  --clear-output \
  --inplace notebooks/duplicate_checks.ipynb
```

These queries are deliberately not part of the CLI yet. See `PLAN.md` for the
future versioned-criteria and migration design.

`notes.md` contains the current MAAP Biomass ingestion and inspection commands.
