-- Data-quality checks: one row per check, with numerator, denominator and rate.
--
-- The checks target the defects documented for the obfuscated GA4 sample (placeholder values such as
-- '<Other>', NULL and '(not set)', and limited internal consistency), plus the defects the
-- modelling layer repairs (missing session ids, duplicate purchases, NULL revenue).

WITH
raw_users AS (
    SELECT
        user_pseudo_id,
        any_value(traffic_source.name)          AS ts_name,
        any_value(geo.country)                  AS country,
        any_value(device.operating_system)      AS os,
        bool_or(user_first_touch_timestamp IS NULL) AS first_touch_null
    FROM events
    WHERE _TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix
    GROUP BY user_pseudo_id
),
raw_events AS (
    SELECT
        count(*)                                                            AS events,
        count(*) FILTER (WHERE NOT list_contains(list_transform(event_params, lambda p: p.key), 'ga_session_id'))
                                                                            AS no_session_id,
        count(*) FILTER (WHERE event_name = 'page_view')                    AS page_views,
        count(*) FILTER (WHERE event_name = 'page_view' AND list_contains(
            list_transform(event_params, lambda p: p.value.string_value), '<Other>'))
                                                                            AS page_other,
        count(*) FILTER (WHERE event_name = 'purchase')                     AS purchases,
        count(*) FILTER (WHERE event_name = 'purchase' AND ecommerce.transaction_id = '(not set)')
                                                                            AS txn_not_set,
        count(*) FILTER (WHERE event_name = 'purchase' AND ecommerce.purchase_revenue_in_usd IS NULL)
                                                                            AS revenue_null
    FROM events
    WHERE _TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix
),
items AS (
    SELECT count(*) AS items, count(*) FILTER (WHERE item_name IS NULL) AS name_not_set
    FROM event_items
),
sess AS (
    SELECT
        count(*)                                                     AS sessions,
        count(*) FILTER (WHERE NOT has_session_start)                AS no_session_start,
        count(*) FILTER (WHERE reached_purchase)                     AS purchase_sessions,
        count(*) FILTER (WHERE reached_purchase AND funnel_depth < 6) AS purchase_incomplete_funnel,
        count(*) FILTER (WHERE events = 1 AND has_repaired_events)   AS orphan_sessions
    FROM sessions
),
usr AS (
    SELECT
        count(*)                                                                  AS users,
        count(*) FILTER (WHERE first_user_channel <> first_session_channel)       AS channel_mismatch
    FROM users
),
txn AS (SELECT count(*) AS transactions FROM transactions),
session_order AS (
    -- ga_session_number should increase with time; count users where it does not.
    SELECT
        count(DISTINCT user_pseudo_id)                                       AS users,
        count(DISTINCT user_pseudo_id) FILTER (WHERE ga_session_number <> rn) AS out_of_order
    FROM (
        SELECT user_pseudo_id, ga_session_number,
               row_number() OVER (PARTITION BY user_pseudo_id ORDER BY session_start_ts) AS rn
        FROM sessions
        WHERE ga_session_number IS NOT NULL
    )
)

SELECT 'traffic_source placeholder (<Other> / (data deleted))' AS check_name, 'users' AS unit,
       count(*) FILTER (WHERE ts_name IN ('<Other>', '(data deleted)')) AS numerator, count(*) AS denominator
FROM raw_users
UNION ALL
SELECT 'geo.country = <Other>', 'users', count(*) FILTER (WHERE country = '<Other>'), count(*) FROM raw_users
UNION ALL
SELECT 'device.operating_system = <Other>', 'users', count(*) FILTER (WHERE os = '<Other>'), count(*) FROM raw_users
UNION ALL
SELECT 'user_first_touch_timestamp NULL', 'users', count(*) FILTER (WHERE first_touch_null), count(*) FROM raw_users
UNION ALL
SELECT 'page_location = <Other>', 'page_view events', page_other, page_views FROM raw_events
UNION ALL
SELECT 'event without ga_session_id', 'events', no_session_id, events FROM raw_events
UNION ALL
SELECT 'transaction_id = (not set)', 'purchase events', txn_not_set, purchases FROM raw_events
UNION ALL
SELECT 'purchase_revenue_in_usd NULL', 'purchase events', revenue_null, purchases FROM raw_events
UNION ALL
SELECT 'duplicate purchase events', 'purchase events', raw_events.purchases - txn.transactions, raw_events.purchases
FROM raw_events, txn
UNION ALL
SELECT 'item_name = (not set)', 'items', name_not_set, items FROM items
UNION ALL
SELECT 'session without session_start', 'sessions', no_session_start, sessions FROM sess
UNION ALL
SELECT 'purchase session missing an earlier checkout step', 'purchase sessions',
       purchase_incomplete_funnel, purchase_sessions FROM sess
UNION ALL
SELECT 'untagged event left as its own session', 'sessions', orphan_sessions, sessions FROM sess
UNION ALL
SELECT 'ga_session_number out of time order', 'users', out_of_order, users FROM session_order
UNION ALL
SELECT 'traffic_source channel <> first-session channel', 'users', channel_mismatch, users FROM usr;
