-- One row per real transaction.
--
-- Two export defects are handled here:
--   * Duplicated purchase events are kept once per dedup key. The key is transaction_id or, when it
--     is '(not set)', the session plus the item revenue. A second identical basket in the same
--     session would be merged too; that is rare and accepted.
--   * NULL purchase_revenue_in_usd is repaired from the sum of item revenue on the same event.

CREATE OR REPLACE TABLE transactions AS
WITH purchases AS (
    SELECT
        event_id,
        session_key,
        user_pseudo_id,
        event_date,
        event_timestamp,
        NULLIF(transaction_id, '(not set)')                 AS transaction_id,
        coalesce(purchase_revenue_usd, items_revenue_usd)   AS revenue_usd,
        purchase_revenue_usd IS NULL                        AS revenue_repaired,
        total_item_quantity,
        n_items,
        CASE
            WHEN transaction_id IS NOT NULL AND transaction_id <> '(not set)' THEN transaction_id
            ELSE session_key || '|' || CAST(round(coalesce(items_revenue_usd, 0), 2) AS VARCHAR)
        END                                                 AS dedup_key
    FROM events_flat
    WHERE event_name = 'purchase'
)
SELECT * EXCLUDE (rn)
FROM (
    SELECT *, row_number() OVER (PARTITION BY dedup_key ORDER BY event_timestamp) AS rn
    FROM purchases
)
WHERE rn = 1;
