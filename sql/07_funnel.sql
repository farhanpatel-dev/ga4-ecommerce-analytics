-- Ecommerce funnel by device, channel and new vs returning, in one pass with GROUPING SETS.
--
-- closed_<step>: sessions that reached the step with every earlier step in order (funnel_depth >= k)
-- open_<step>:   sessions where the step's event happened at all
-- Sessions are the unit; funnel.py adds user-clustered uncertainty because one user can
-- contribute several sessions.

CREATE OR REPLACE TABLE funnel_segments AS
SELECT
    CASE
        WHEN GROUPING(device_category) = 0 THEN 'device'
        WHEN GROUPING(channel) = 0 THEN 'channel'
        WHEN GROUPING(user_type) = 0 THEN 'user_type'
        ELSE 'all'
    END                                                         AS dimension,
    CASE
        WHEN GROUPING(device_category) = 0 THEN device_category
        WHEN GROUPING(channel) = 0 THEN channel
        WHEN GROUPING(user_type) = 0 THEN user_type
        ELSE 'All'
    END                                                         AS segment,
    count(*)                                                    AS sessions,
    count(DISTINCT user_pseudo_id)                              AS users,
    count(*) FILTER (WHERE funnel_depth >= 1)                   AS closed_view_item,
    count(*) FILTER (WHERE funnel_depth >= 2)                   AS closed_add_to_cart,
    count(*) FILTER (WHERE funnel_depth >= 3)                   AS closed_begin_checkout,
    count(*) FILTER (WHERE funnel_depth >= 4)                   AS closed_add_shipping_info,
    count(*) FILTER (WHERE funnel_depth >= 5)                   AS closed_add_payment_info,
    count(*) FILTER (WHERE funnel_depth >= 6)                   AS closed_purchase,
    count(*) FILTER (WHERE reached_view_item)                   AS open_view_item,
    count(*) FILTER (WHERE reached_add_to_cart)                 AS open_add_to_cart,
    count(*) FILTER (WHERE reached_begin_checkout)              AS open_begin_checkout,
    count(*) FILTER (WHERE reached_add_shipping_info)           AS open_add_shipping_info,
    count(*) FILTER (WHERE reached_add_payment_info)            AS open_add_payment_info,
    count(*) FILTER (WHERE reached_purchase)                    AS open_purchase
FROM sessions
WHERE strftime(session_date, '%Y%m%d') BETWEEN $start_suffix AND $end_suffix
GROUP BY GROUPING SETS ((), (device_category), (channel), (user_type));
