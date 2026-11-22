-- Session-level table: one row per (user_pseudo_id, ga_session_id).
--
-- * Channel comes from the session's own source/medium (event_params), NOT traffic_source. The
--   latter is the user's first touch and would turn every session into first-click attribution.
-- * Funnel flags come in two flavours:
--     reached_<step>  open funnel: the event happened in the session
--     funnel_depth    closed funnel: how many steps happened in order (0..6)
-- * session_number falls back to the user's chronological session order when ga_session_number
--   is missing on every event of a session.

CREATE OR REPLACE TABLE sessions AS
WITH agg AS (
    SELECT
        session_key,
        any_value(user_pseudo_id)                                          AS user_pseudo_id,
        any_value(ga_session_id)                                           AS ga_session_id,
        max(ga_session_number)                                             AS ga_session_number,
        min(event_ts)                                                      AS session_start_ts,
        max(event_ts)                                                      AS session_end_ts,
        min(event_date)                                                    AS session_date,
        arg_min(source, event_timestamp) FILTER (WHERE source IS NOT NULL)     AS source,
        arg_min(medium, event_timestamp) FILTER (WHERE medium IS NOT NULL)     AS medium,
        arg_min(campaign, event_timestamp) FILTER (WHERE campaign IS NOT NULL) AS campaign,
        any_value(device_category)                                         AS device_category,
        any_value(operating_system)                                        AS operating_system,
        any_value(country)                                                 AS country,
        count(*)                                                           AS events,
        count(*) FILTER (WHERE event_name = 'page_view')                   AS page_views,
        coalesce(sum(engagement_time_msec), 0)                             AS engagement_time_msec,
        bool_or(event_name = 'session_start')                              AS has_session_start,
        bool_or(session_id_repaired)                                       AS has_repaired_events,
        min(event_timestamp) FILTER (WHERE event_name = 'view_item')         AS ts_view_item,
        min(event_timestamp) FILTER (WHERE event_name = 'add_to_cart')       AS ts_add_to_cart,
        min(event_timestamp) FILTER (WHERE event_name = 'begin_checkout')    AS ts_begin_checkout,
        min(event_timestamp) FILTER (WHERE event_name = 'add_shipping_info') AS ts_add_shipping_info,
        min(event_timestamp) FILTER (WHERE event_name = 'add_payment_info')  AS ts_add_payment_info,
        min(event_timestamp) FILTER (WHERE event_name = 'purchase')          AS ts_purchase
    FROM events_flat
    GROUP BY session_key
),
numbered AS (
    SELECT
        *,
        coalesce(ga_session_number,
                 row_number() OVER (PARTITION BY user_pseudo_id ORDER BY session_start_ts)) AS session_number
    FROM agg
),
txn AS (
    SELECT session_key, count(*) AS transactions, sum(revenue_usd) AS revenue_usd
    FROM transactions
    GROUP BY session_key
)
SELECT
    n.* EXCLUDE (ts_view_item, ts_add_to_cart, ts_begin_checkout, ts_add_shipping_info,
                 ts_add_payment_info, ts_purchase),
    CASE WHEN n.session_number = 1 THEN 'new' ELSE 'returning' END     AS user_type,
    channel_group(n.source, n.medium)                                  AS channel,
    date_diff('second', n.session_start_ts, n.session_end_ts)          AS duration_s,
    n.ts_view_item IS NOT NULL                                         AS reached_view_item,
    n.ts_add_to_cart IS NOT NULL                                       AS reached_add_to_cart,
    n.ts_begin_checkout IS NOT NULL                                    AS reached_begin_checkout,
    n.ts_add_shipping_info IS NOT NULL                                 AS reached_add_shipping_info,
    n.ts_add_payment_info IS NOT NULL                                  AS reached_add_payment_info,
    n.ts_purchase IS NOT NULL                                          AS reached_purchase,
    -- Closed funnel: each step must exist and come no earlier than the previous one.
    CASE
        WHEN n.ts_view_item IS NULL THEN 0
        WHEN n.ts_add_to_cart IS NULL OR n.ts_add_to_cart < n.ts_view_item THEN 1
        WHEN n.ts_begin_checkout IS NULL OR n.ts_begin_checkout < n.ts_add_to_cart THEN 2
        WHEN n.ts_add_shipping_info IS NULL OR n.ts_add_shipping_info < n.ts_begin_checkout THEN 3
        WHEN n.ts_add_payment_info IS NULL OR n.ts_add_payment_info < n.ts_add_shipping_info THEN 4
        WHEN n.ts_purchase IS NULL OR n.ts_purchase < n.ts_add_payment_info THEN 5
        ELSE 6
    END                                                                AS funnel_depth,
    coalesce(t.transactions, 0)                                        AS transactions,
    coalesce(t.revenue_usd, 0)                                         AS revenue_usd
FROM numbered AS n
LEFT JOIN txn AS t USING (session_key);
