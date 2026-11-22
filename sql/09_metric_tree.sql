-- Daily inputs for the revenue metric tree.
--
--   revenue = users x conversion x AOV
--           = users x (sessions / user) x (transactions / session) x (items / transaction) x (revenue / item)
--             \___/   \__________________ conversion _____________/   \____________ AOV ______________/
--
-- users splits additively into new and returning. A "returning user" here is an active user with no
-- first session that day.

CREATE OR REPLACE TABLE metric_tree_daily AS
WITH items AS (
    SELECT session_key, sum(total_item_quantity) AS items_sold
    FROM transactions
    GROUP BY session_key
)
SELECT
    s.session_date                                                     AS date,
    count(DISTINCT s.user_pseudo_id)                                   AS users,
    count(DISTINCT s.user_pseudo_id) FILTER (WHERE s.user_type = 'new') AS new_users,
    count(DISTINCT s.user_pseudo_id)
        - count(DISTINCT s.user_pseudo_id) FILTER (WHERE s.user_type = 'new') AS returning_users,
    count(*)                                                           AS sessions,
    sum(s.transactions)                                                AS transactions,
    coalesce(sum(i.items_sold), 0)                                     AS items_sold,
    round(sum(s.revenue_usd), 2)                                       AS revenue_usd
FROM sessions AS s
LEFT JOIN items AS i USING (session_key)
WHERE strftime(s.session_date, '%Y%m%d') BETWEEN $start_suffix AND $end_suffix
GROUP BY s.session_date
ORDER BY s.session_date;
