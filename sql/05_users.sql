-- User-level table: one row per user_pseudo_id.
--
-- first_user_channel is GA4's traffic_source (first touch, as exported). first_session_channel is
-- rebuilt from the user's first session. They should agree; where they do not, the export's
-- obfuscation ('<Other>', '(data deleted)') is usually the reason.

CREATE OR REPLACE TABLE users AS
WITH ordered AS (
    SELECT *, row_number() OVER (PARTITION BY user_pseudo_id ORDER BY session_start_ts) AS rn
    FROM sessions
),
first_purchase AS (
    SELECT user_pseudo_id,
           min(session_start_ts) AS first_purchase_ts,
           min(rn)               AS sessions_to_first_purchase
    FROM ordered
    WHERE transactions > 0
    GROUP BY user_pseudo_id
),
traffic AS (
    SELECT user_pseudo_id,
           any_value(first_user_source) AS first_user_source,
           any_value(first_user_medium) AS first_user_medium
    FROM events_flat
    GROUP BY user_pseudo_id
)
SELECT
    o.user_pseudo_id,
    min(o.session_date)                                            AS first_seen_date,
    max(o.session_date)                                            AS last_seen_date,
    count(*)                                                       AS sessions,
    any_value(o.device_category)                                   AS device_category,
    any_value(o.country)                                           AS country,
    arg_min(o.channel, o.session_start_ts)                         AS first_session_channel,
    arg_max(o.channel, o.session_start_ts)                         AS last_session_channel,
    channel_group(tr.first_user_source, tr.first_user_medium)      AS first_user_channel,
    sum(o.page_views)                                              AS page_views,
    sum(o.transactions)                                            AS transactions,
    sum(o.revenue_usd)                                             AS revenue_usd,
    sum(o.transactions) > 0                                        AS purchased,
    fp.first_purchase_ts,
    fp.sessions_to_first_purchase,
    date_diff('day', min(o.session_start_ts), fp.first_purchase_ts) AS days_to_first_purchase
FROM ordered AS o
LEFT JOIN first_purchase AS fp USING (user_pseudo_id)
LEFT JOIN traffic AS tr USING (user_pseudo_id)
GROUP BY o.user_pseudo_id, tr.first_user_source, tr.first_user_medium,
         fp.first_purchase_ts, fp.sessions_to_first_purchase;
