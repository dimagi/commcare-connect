SELECT
	ou.opportunity_id,
	ou.xform_id AS visit_id,
	ou.visit_date,
	ou.form_json -> 'metadata' -> 'username' AS username_connectid,
	COALESCE(ou.form_json -> 'form' -> 'case' -> 'update' ->> 'visit_type', 'unknown') AS visit_type,
	du.name AS deliver_unit_name,
    	-- New column: days_since_opp_start
	DATE_PART('day', ou.visit_date::timestamp - opp.start_date::timestamp) AS days_since_opp_start,
	ou.entity_id,
	ou.status,
	ou.flagged,
	ou.flag_reason,
	ou.reason AS rejection_reason,
	ou.form_json -> 'metadata' -> 'timeStart' AS timeStart,
	ou.form_json -> 'metadata' -> 'timeEnd' AS timeEnd,
	EXTRACT(EPOCH FROM ((ou.form_json -> 'metadata' ->> 'timeEnd')::timestamp - (ou.form_json -> 'metadata' ->> 'timeStart')::timestamp)) / 60 AS visit_duration,
	gs_closest.gs_visit_1_date,
	gs_closest.gs_visit_2_date,
	gs_closest.gs_visit_1_score,
	gs_closest.gs_visit_2_score,
	gs_closest.gs_visit_1_formid,
	gs_closest.gs_visit_2_formid,

	CASE
	WHEN NULLIF(ou.form_json -> 'form' -> 'case' -> 'update' ->> 'confirm_practice_case', '') IS NOT NULL
	THEN 'yes'
	ELSE NULL
  END AS practice_case,
	CASE
    	WHEN DATE(ou.visit_date) < COALESCE(DATE(gs_closest.gs_visit_1_date::timestamp), DATE(gs_closest.gs_visit_2_date::timestamp)) THEN 'before_gs1'
    	WHEN DATE(ou.visit_date) = DATE(gs_closest.gs_visit_1_date::timestamp) THEN 'gs1'
    	WHEN DATE(ou.visit_date) > DATE(gs_closest.gs_visit_1_date::timestamp) AND gs_closest.gs_visit_2_date IS NULL THEN 'after_gs1_no_gs2'
    	WHEN DATE(ou.visit_date) > COALESCE(DATE(gs_closest.gs_visit_1_date::timestamp), DATE(gs_closest.gs_visit_2_date::timestamp)) THEN 'after_gs1'
    	WHEN DATE(ou.visit_date) < COALESCE(DATE(gs_closest.gs_visit_2_date::timestamp), DATE(gs_closest.gs_visit_1_date::timestamp)) THEN 'before_gs2'
    	WHEN DATE(ou.visit_date) > DATE(gs_closest.gs_visit_1_date::timestamp) AND DATE(ou.visit_date) < DATE(gs_closest.gs_visit_2_date::timestamp) THEN 'between_gs1_gs2'
    	WHEN DATE(ou.visit_date) = DATE(gs_closest.gs_visit_2_date::timestamp) THEN 'gs2'
    	WHEN DATE(ou.visit_date) > COALESCE(DATE(gs_closest.gs_visit_2_date::timestamp), DATE(gs_closest.gs_visit_1_date::timestamp)) THEN 'after_gs2'
    	ELSE 'no_match'
	END AS "GS Analysis Visit Type",
	CASE
    	WHEN ou.form_json -> 'metadata' -> 'location' IS NOT NULL THEN
        	SPLIT_PART(ou.form_json -> 'metadata' ->> 'location', ' ', 1)
    	ELSE NULL
	END AS gps_location_lat,
	-- New column for longitude
	CASE
    	WHEN ou.form_json -> 'metadata' -> 'location' IS NOT NULL THEN
        	SPLIT_PART(ou.form_json -> 'metadata' ->> 'location', ' ', 2)
    	ELSE NULL
	END AS gps_location_long

FROM opportunity_uservisit ou
LEFT JOIN LATERAL (
	SELECT
    	MAX(CASE
        	WHEN gs."gs_visit_number.which_gold_standard_visit_are_you_assessing" = 'visit_1'
        	THEN gs.received_on
        	ELSE NULL
    	END) AS gs_visit_1_date,
    	MAX(CASE
        	WHEN gs."gs_visit_number.which_gold_standard_visit_are_you_assessing" = 'visit_2'
        	THEN gs.received_on
        	ELSE NULL
    	END) AS gs_visit_2_date,
    	MAX(CASE
        	WHEN gs."gs_visit_number.which_gold_standard_visit_are_you_assessing" = 'visit_1'
        	THEN gs."form.checklist_percentage"
        	ELSE NULL
    	END) AS gs_visit_1_score,
    	MAX(CASE
        	WHEN gs."gs_visit_number.which_gold_standard_visit_are_you_assessing" = 'visit_2'
        	THEN gs."form.checklist_percentage"
        	ELSE NULL
    	END) AS gs_visit_2_score,
    	MAX(CASE
        	WHEN gs."gs_visit_number.which_gold_standard_visit_are_you_assessing" = 'visit_1'
        	THEN gs.formid
        	ELSE NULL
    	END) AS gs_visit_1_formid,
    	MAX(CASE
        	WHEN gs."gs_visit_number.which_gold_standard_visit_are_you_assessing" = 'visit_2'
        	THEN gs.formid
        	ELSE NULL
    	END) AS gs_visit_2_formid
	FROM public.cchq_gs_forms gs
	WHERE LOWER(gs."form.load_flw_connect_id") = ou.form_json -> 'metadata' ->> 'username'
	GROUP BY LOWER(gs."form.load_flw_connect_id")
) gs_closest ON TRUE
LEFT JOIN opportunity_deliverunit du ON du.id = ou.deliver_unit_id
LEFT JOIN opportunity_opportunity opp ON opp.id = ou.opportunity_id;
