-- One row per event, with the nested event_params pulled out by UNNEST.
--
-- BigQuery equivalent of the source:
--   FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
--   WHERE _TABLE_SUFFIX BETWEEN @start_suffix AND @end_suffix
-- Here `events` is a view over data/events/events_YYYYMMDD.parquet with the same _TABLE_SUFFIX.
--
-- Session repair: about 1% of events arrive without ga_session_id. Each one is assigned to the
-- user's nearest tagged event within 30 minutes (the GA4 session timeout), looking back first and
-- then forward. Anything still unmatched becomes its own one-event session.

CREATE OR REPLACE TABLE events_flat AS
WITH params AS (
    -- One UNNEST pass over event_params, pivoted back to one row per event with conditional
    -- aggregation. This is about 50x faster than one correlated UNNEST subquery per key (the
    -- param_str / param_int macros), which is fine for ad-hoc queries but not for a full rebuild.
    SELECT
        hash(e.user_pseudo_id, e.event_timestamp, e.event_name)                 AS event_id,
        max(p.value.int_value)    FILTER (WHERE p.key = 'ga_session_id')        AS ga_session_id_raw,
        max(p.value.int_value)    FILTER (WHERE p.key = 'ga_session_number')    AS ga_session_number_raw,
        max(p.value.string_value) FILTER (WHERE p.key = 'source')               AS source,
        max(p.value.string_value) FILTER (WHERE p.key = 'medium')               AS medium,
        max(p.value.string_value) FILTER (WHERE p.key = 'campaign')             AS campaign,
        max(p.value.string_value) FILTER (WHERE p.key = 'page_location')        AS page_location,
        max(p.value.int_value)    FILTER (WHERE p.key = 'engagement_time_msec') AS engagement_time_msec
    FROM events AS e, unnest(e.event_params) AS t(p)
    WHERE e._TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix
    GROUP BY 1
),
base AS (
    SELECT
        _TABLE_SUFFIX                                     AS table_suffix,
        strptime(event_date, '%Y%m%d')::DATE              AS event_date,
        event_timestamp,
        make_timestamp(event_timestamp)                   AS event_ts,
        event_name,
        user_pseudo_id,
        hash(user_pseudo_id, event_timestamp, event_name) AS event_id,
        prm.ga_session_id_raw,
        prm.ga_session_number_raw,
        prm.source,
        prm.medium,
        prm.campaign,
        prm.page_location,
        prm.engagement_time_msec,
        device.category                                   AS device_category,
        device.operating_system                           AS operating_system,
        device.web_info.browser                           AS browser,
        geo.country                                       AS country,
        traffic_source.source                             AS first_user_source,
        traffic_source.medium                             AS first_user_medium,
        traffic_source.name                               AS first_user_campaign,
        ecommerce.transaction_id                          AS transaction_id,
        ecommerce.purchase_revenue_in_usd                 AS purchase_revenue_usd,
        ecommerce.total_item_quantity                     AS total_item_quantity,
        (SELECT sum(i.item_revenue_in_usd) FROM unnest(items) AS v(i)) AS items_revenue_usd,
        len(items)                                        AS n_items
    FROM events
    LEFT JOIN params AS prm
        ON prm.event_id = hash(events.user_pseudo_id, events.event_timestamp, events.event_name)
    WHERE _TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix
),
neighbours AS (
    SELECT
        *,
        last_value(ga_session_id_raw IGNORE NULLS) OVER w_back     AS prev_sid,
        last_value(CASE WHEN ga_session_id_raw IS NOT NULL THEN event_timestamp END IGNORE NULLS)
            OVER w_back                                              AS prev_sid_ts,
        last_value(ga_session_number_raw IGNORE NULLS) OVER w_back  AS prev_snum,
        first_value(ga_session_id_raw IGNORE NULLS) OVER w_fwd      AS next_sid,
        first_value(CASE WHEN ga_session_id_raw IS NOT NULL THEN event_timestamp END IGNORE NULLS)
            OVER w_fwd                                               AS next_sid_ts,
        first_value(ga_session_number_raw IGNORE NULLS) OVER w_fwd  AS next_snum
    FROM base
    WINDOW
        w_back AS (PARTITION BY user_pseudo_id ORDER BY event_timestamp
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
        w_fwd  AS (PARTITION BY user_pseudo_id ORDER BY event_timestamp
                   ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)
),
repaired AS (
    SELECT
        * EXCLUDE (prev_sid, prev_sid_ts, prev_snum, next_sid, next_sid_ts, next_snum),
        ga_session_id_raw IS NULL AS session_id_repaired,
        CASE
            WHEN ga_session_id_raw IS NOT NULL THEN ga_session_id_raw
            WHEN event_timestamp - prev_sid_ts <= 30 * 60 * 1000000 THEN prev_sid
            WHEN next_sid_ts - event_timestamp <= 30 * 60 * 1000000 THEN next_sid
            ELSE event_timestamp // 1000000
        END AS ga_session_id,
        CASE
            WHEN ga_session_id_raw IS NOT NULL THEN ga_session_number_raw
            WHEN event_timestamp - prev_sid_ts <= 30 * 60 * 1000000 THEN prev_snum
            WHEN next_sid_ts - event_timestamp <= 30 * 60 * 1000000 THEN next_snum
        END AS ga_session_number
    FROM neighbours
)
SELECT
    *,
    user_pseudo_id || '_' || CAST(ga_session_id AS VARCHAR) AS session_key
FROM repaired;
