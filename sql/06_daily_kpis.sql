-- Daily KPI table: the input to the metric tree and the anomaly detector.
-- conversion_rate is transactions per active user, so revenue = users x conversion_rate x AOV
-- holds exactly.

CREATE OR REPLACE TABLE daily_kpis AS
SELECT
    session_date                                                    AS date,
    count(DISTINCT user_pseudo_id)                                  AS users,
    count(DISTINCT user_pseudo_id) FILTER (WHERE user_type = 'new') AS new_users,
    count(*)                                                        AS sessions,
    count(*) FILTER (WHERE reached_add_to_cart)                     AS cart_sessions,
    count(*) FILTER (WHERE reached_purchase)                        AS purchase_sessions,
    count(DISTINCT user_pseudo_id) FILTER (WHERE transactions > 0)  AS purchasers,
    sum(transactions)                                               AS transactions,
    round(sum(revenue_usd), 2)                                      AS revenue_usd,
    sum(transactions) / count(DISTINCT user_pseudo_id)              AS conversion_rate,
    count(*) FILTER (WHERE reached_purchase) / count(*)             AS session_conversion_rate,
    sum(revenue_usd) / NULLIF(sum(transactions), 0)                 AS aov_usd
FROM sessions
GROUP BY session_date
ORDER BY session_date;
