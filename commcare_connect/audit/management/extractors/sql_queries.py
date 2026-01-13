"""
SQL Queries for Superset Data Extraction

This module contains predefined SQL queries that can be used with the SupersetExtractor.
"""

# 9/19/2025 Connect Location analysis
SQL_CONNECT_LOCATION_ANALYSIS = """
WITH user_recent_visits AS (
  SELECT
    uv.user_id,
    MAX(uv.visit_date) as most_recent_visit_date
  FROM opportunity_uservisit uv
  LEFT JOIN opportunity_opportunity oo ON oo.id = uv.opportunity_id
  WHERE uv.location IS NOT NULL
    AND uv.location != ''
    AND TRIM(uv.location) != ''
    AND oo.is_test = FALSE
  GROUP BY uv.user_id
),
visits_with_gps AS (
  SELECT
    uv.user_id AS flw_id,
    users_user.name AS flw_name,
    uv.visit_date,
    uv.opportunity_id as opp_id,
    oo.name as opp_name,
    SPLIT_PART(uv.location,' ',1) AS latitude,
    SPLIT_PART(uv.location,' ',2) AS longitude,
    SPLIT_PART(uv.location,' ',3) AS elevation_in_m,
    CAST(SPLIT_PART(uv.location,' ',4) AS NUMERIC) AS accuracy_in_m,
    ROW_NUMBER() OVER (
      PARTITION BY uv.user_id
      ORDER BY CAST(SPLIT_PART(uv.location,' ',4) AS NUMERIC) ASC
    ) as accuracy_rank
  FROM opportunity_uservisit uv
  LEFT JOIN opportunity_opportunity oo ON oo.id = uv.opportunity_id
  LEFT JOIN users_user ON users_user.id = uv.user_id
  INNER JOIN user_recent_visits urv ON urv.user_id = uv.user_id
  WHERE
    uv.location IS NOT NULL
    AND uv.location != ''
    AND TRIM(uv.location) != ''
    AND uv.visit_date >= urv.most_recent_visit_date - INTERVAL '30 days'
    AND SPLIT_PART(uv.location,' ',4) ~ '^[0-9.]+$'
    AND oo.is_test = FALSE
)
SELECT
  flw_id,
  opp_id,
  opp_name,
  visit_date as date,
  latitude,
  longitude,
  elevation_in_m,
  accuracy_in_m
FROM visits_with_gps
WHERE accuracy_rank = 1
ORDER BY flw_id;
"""

# 9/5/2025 Fake Data Analysis
SQL_FAKE_DATA_PARTY = """
SELECT
   opportunity_uservisit.opportunity_id AS opportunity_id,
   opportunity_uservisit.user_id AS flw_id,
   form_json

FROM opportunity_uservisit
LEFT JOIN opportunity_opportunity
 ON opportunity_opportunity.id = opportunity_uservisit.opportunity_id
WHERE opportunity_opportunity.id IN (716,715);
"""

SQL_ALL_DATA_QUERY = """
SELECT
   opportunity_uservisit.opportunity_id AS opportunity_id,
   opportunity_uservisit.user_id AS flw_id,
   form_json

FROM opportunity_uservisit
LEFT JOIN opportunity_opportunity
 ON opportunity_opportunity.id = opportunity_uservisit.opportunity_id
WHERE opportunity_opportunity.id IN (601, 575, 597, 531, 412, 516, 598, 604, 573, 595, 566, 539, 411, 579, 517);
"""
# End Fake Data Analysis
