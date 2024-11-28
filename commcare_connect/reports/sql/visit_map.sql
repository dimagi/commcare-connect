SELECT
	ou.opportunity_id,
	ou.xform_id AS visit_id,
	ou.visit_date,
	ou.form_json -> 'metadata' -> 'username' AS username_connectid,
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
LEFT JOIN opportunity_deliverunit du ON du.id = ou.deliver_unit_id
LEFT JOIN opportunity_opportunity opp ON opp.id = ou.opportunity_id;
