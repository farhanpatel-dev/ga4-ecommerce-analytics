-- One row per (event, item): the items array flattened with a lateral UNNEST.
-- BigQuery equivalent: FROM `...events_*` AS e, UNNEST(e.items) AS i

CREATE OR REPLACE TABLE event_items AS
SELECT
    hash(e.user_pseudo_id, e.event_timestamp, e.event_name) AS event_id,
    e.event_name,
    e.user_pseudo_id,
    strptime(e.event_date, '%Y%m%d')::DATE                 AS event_date,
    i.item_id,
    NULLIF(i.item_name, '(not set)')                       AS item_name,
    NULLIF(i.item_category, '(not set)')                   AS item_category,
    i.price_in_usd,
    i.quantity,
    i.item_revenue_in_usd
FROM events AS e, unnest(e.items) AS t(i)
WHERE e._TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix;
