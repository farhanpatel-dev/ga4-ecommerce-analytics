-- Customer journeys for multi-touch attribution: one row per user.
--
-- A journey is the user's session channels in time order, up to and including the session of their
-- first purchase (converters), or all their sessions in the window (non-converters). Later sessions
-- after a first purchase belong to a new journey and are left out here.
--
-- Caveats that come from the data, not the method:
--   * The window starts on 2020-11-01, so journeys already under way are cut at the left edge
--     (their earlier touches are invisible), and journeys still open on 2021-01-31 count as
--     non-converting.
--   * user_pseudo_id is a cookie, so one person on two devices is two journeys.

CREATE OR REPLACE TABLE attribution_paths AS
WITH windowed AS (
    SELECT
        user_pseudo_id,
        session_start_ts,
        channel,
        transactions,
        revenue_usd,
        min(CASE WHEN transactions > 0 THEN session_start_ts END)
            OVER (PARTITION BY user_pseudo_id) AS first_purchase_ts
    FROM sessions
    WHERE strftime(session_date, '%Y%m%d') BETWEEN $start_suffix AND $end_suffix
)
SELECT
    user_pseudo_id,
    list(channel ORDER BY session_start_ts)                                  AS path,
    count(*)                                                                 AS touches,
    any_value(first_purchase_ts) IS NOT NULL                                 AS converted,
    coalesce(sum(revenue_usd) FILTER (WHERE session_start_ts = first_purchase_ts), 0) AS revenue_usd
FROM windowed
WHERE first_purchase_ts IS NULL OR session_start_ts <= first_purchase_ts
GROUP BY user_pseudo_id;

-- The same journeys collapsed to distinct paths, the usual input format for path-based models.
CREATE OR REPLACE TABLE attribution_path_counts AS
SELECT
    array_to_string(path, ' > ')                 AS path_str,
    any_value(path)                              AS path,
    count(*)                                     AS journeys,
    count(*) FILTER (WHERE converted)            AS conversions,
    count(*) FILTER (WHERE NOT converted)        AS non_conversions,
    round(sum(revenue_usd), 2)                   AS revenue_usd
FROM attribution_paths
GROUP BY path
ORDER BY journeys DESC;
