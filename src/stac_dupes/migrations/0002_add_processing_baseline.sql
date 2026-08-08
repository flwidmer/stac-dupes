ALTER TABLE items ADD COLUMN processing_baseline TEXT;

UPDATE items
SET processing_baseline = item #>> '{properties,version}'
WHERE item #>> '{properties,version}' IS NOT NULL;

CREATE INDEX items_processing_baseline_idx ON items (processing_baseline);
