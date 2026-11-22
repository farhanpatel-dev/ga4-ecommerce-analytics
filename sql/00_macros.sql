-- Reusable SQL macros (DuckDB). BigQuery equivalent: CREATE TEMP FUNCTION.

-- Default channel grouping, a simplified version of GA4's rules, from session source/medium.
-- Rule order matters: e.g. facebook.com / social must hit Organic Social before Referral.
CREATE OR REPLACE MACRO channel_group(src, med) AS
    CASE
        WHEN src IS NULL AND med IS NULL THEN 'Unassigned'
        WHEN src IN ('<Other>', '(data deleted)') OR med IN ('<Other>', '(data deleted)') THEN 'Unassigned'
        WHEN src = '(direct)' AND med IN ('(none)', '(not set)') THEN 'Direct'
        WHEN med IN ('cpc', 'ppc', 'paid', 'paidsearch') THEN 'Paid Search'
        WHEN med IN ('display', 'cpm', 'banner', 'interstitial') THEN 'Display'
        WHEN med = 'organic' THEN 'Organic Search'
        WHEN med = 'social'
          OR regexp_matches(src, 'facebook|instagram|youtube|twitter|linkedin|reddit|pinterest') THEN 'Organic Social'
        WHEN med = 'email' OR src ILIKE '%newsletter%' THEN 'Email'
        WHEN med = 'affiliate' THEN 'Affiliates'
        WHEN med = 'referral' THEN 'Referral'
        ELSE 'Unassigned'
    END;

-- One event parameter by key, from the GA4 event_params array.
-- BigQuery equivalent: (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'x')
CREATE OR REPLACE MACRO param_str(params, k) AS
    (SELECT p.value.string_value FROM unnest(params) AS u(p) WHERE p.key = k LIMIT 1);

CREATE OR REPLACE MACRO param_int(params, k) AS
    (SELECT p.value.int_value FROM unnest(params) AS u(p) WHERE p.key = k LIMIT 1);
