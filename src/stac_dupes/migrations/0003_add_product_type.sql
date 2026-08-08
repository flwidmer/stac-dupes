ALTER TABLE items ADD COLUMN product_type TEXT;

UPDATE items
SET product_type = item #>> '{properties,product:type}'
WHERE item #>> '{properties,product:type}' IS NOT NULL;

CREATE INDEX items_product_type_idx ON items (product_type);
