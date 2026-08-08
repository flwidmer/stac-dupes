-- Candidate pairs whose sensing intervals and geometries overlap.
SELECT
    a.id AS item_a_id,
    a.catalog_url AS item_a_catalog,
    a.stac_id AS item_a_stac_id,
    a.processing_baseline,
    a.product_type,
    b.id AS item_b_id,
    b.catalog_url AS item_b_catalog,
    b.stac_id AS item_b_stac_id
FROM items AS a
JOIN items AS b
    ON a.id < b.id
   AND a.processing_baseline IS NOT DISTINCT FROM b.processing_baseline
   AND a.product_type IS NOT DISTINCT FROM b.product_type
   AND tstzrange(a.sensing_start, a.sensing_end, '[]')
       && tstzrange(b.sensing_start, b.sensing_end, '[]')
   AND ST_Intersects(a.geometry, b.geometry);

-- Exact geometry and sensing-time matches.
SELECT
    a.id AS item_a_id,
    b.id AS item_b_id,
    a.stac_id AS item_a_stac_id,
    b.stac_id AS item_b_stac_id,
    a.processing_baseline,
    a.product_type
FROM items AS a
JOIN items AS b
    ON a.id < b.id
   AND a.processing_baseline IS NOT DISTINCT FROM b.processing_baseline
   AND a.product_type IS NOT DISTINCT FROM b.product_type
   AND a.sensing_start IS NOT DISTINCT FROM b.sensing_start
   AND a.sensing_end IS NOT DISTINCT FROM b.sensing_end
   AND ST_Equals(a.geometry, b.geometry);

-- Reused STAC IDs from different catalog roots.
SELECT
    a.stac_id,
    a.processing_baseline,
    a.product_type,
    a.catalog_url AS catalog_a,
    b.catalog_url AS catalog_b
FROM items AS a
JOIN items AS b
    ON a.id < b.id
   AND a.stac_id = b.stac_id
   AND a.catalog_url <> b.catalog_url
   AND a.processing_baseline IS NOT DISTINCT FROM b.processing_baseline
   AND a.product_type IS NOT DISTINCT FROM b.product_type;
