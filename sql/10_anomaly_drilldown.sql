-- Drill-down for one anomalous day against a baseline of nearby normal days.
--
-- $anomaly_date   the day under investigation (DATE)
-- $baseline_days  half-width of the baseline window in days; the anomaly day itself is excluded
--
-- Returns funnel counts per (dimension, segment, period) in one pass with GROUPING SETS, where
-- period is 'anomaly' or 'baseline'. Baseline counts are totals over the window; anomaly.py turns
-- them into rates and expected values.

SELECT
    CASE WHEN session_date = $anomaly_date THEN 'anomaly' ELSE 'baseline' END AS period,
    CASE
        WHEN GROUPING(device_category) = 0 AND GROUPING(channel) = 1 THEN 'device'
        WHEN GROUPING(channel) = 0 THEN 'channel'
        WHEN GROUPING(user_type) = 0 THEN 'user_type'
        WHEN GROUPING(operating_system) = 0 THEN 'operating_system'
        ELSE 'all'
    END                                                       AS dimension,
    CASE
        WHEN GROUPING(device_category) = 0 AND GROUPING(channel) = 1 THEN device_category
        WHEN GROUPING(channel) = 0 THEN channel
        WHEN GROUPING(user_type) = 0 THEN user_type
        WHEN GROUPING(operating_system) = 0 THEN operating_system
        ELSE 'All'
    END                                                       AS segment,
    count(DISTINCT session_date)                              AS days,
    count(*)                                                  AS sessions,
    count(*) FILTER (WHERE reached_view_item)                 AS view_item,
    count(*) FILTER (WHERE reached_add_to_cart)               AS add_to_cart,
    count(*) FILTER (WHERE reached_begin_checkout)            AS begin_checkout,
    count(*) FILTER (WHERE reached_add_shipping_info)         AS add_shipping_info,
    count(*) FILTER (WHERE reached_add_payment_info)          AS add_payment_info,
    count(*) FILTER (WHERE reached_purchase)                  AS purchase,
    sum(transactions)                                         AS transactions,
    sum(revenue_usd)                                          AS revenue_usd
FROM sessions
WHERE session_date BETWEEN $anomaly_date - to_days($baseline_days) AND $anomaly_date + to_days($baseline_days)
GROUP BY GROUPING SETS (
    (period),
    (period, device_category),
    (period, channel),
    (period, user_type),
    (period, operating_system)
)
ORDER BY dimension, segment, period;
