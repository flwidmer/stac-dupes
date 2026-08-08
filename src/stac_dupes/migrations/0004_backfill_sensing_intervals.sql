UPDATE items
SET
    sensing_start = (item #>> '{properties,start_datetime}')::timestamptz,
    sensing_end = (item #>> '{properties,end_datetime}')::timestamptz
WHERE NULLIF(item #>> '{properties,start_datetime}', '') IS NOT NULL
  AND NULLIF(item #>> '{properties,end_datetime}', '') IS NOT NULL
  AND (
      sensing_start IS DISTINCT FROM
          (item #>> '{properties,start_datetime}')::timestamptz
      OR sensing_end IS DISTINCT FROM
          (item #>> '{properties,end_datetime}')::timestamptz
  );
