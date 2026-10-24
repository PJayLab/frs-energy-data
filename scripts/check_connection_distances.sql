-- Read-only PostgreSQL/PostGIS audit of connected objects.
-- All distances are straight-line metres on the WGS84 spheroid, not pipe lengths.
-- Change max_distance_m below to adjust the threshold.
-- Only suspicious or incomplete connections are returned. No data is modified.

WITH settings AS (
    SELECT 1000.0::double precision AS max_distance_m
),
object_checks AS (
    SELECT
        o.*,
        CASE
            WHEN o.geom IS NULL THEN 'missing_geometry'
            WHEN ST_IsEmpty(o.geom) THEN 'empty_geometry'
            WHEN ST_SRID(o.geom) <> 4326 THEN 'unexpected_srid'
            WHEN ST_GeometryType(o.geom) <> 'ST_Point' THEN 'not_a_point'
            WHEN NOT ST_IsValid(o.geom) THEN 'invalid_geometry'
            WHEN NOT (ST_X(o.geom) BETWEEN -180 AND 180
                  AND ST_Y(o.geom) BETWEEN -90 AND 90) THEN 'invalid_coordinates'
            ELSE 'ok'
        END AS coordinate_status
    FROM objects o
),
checked_objects AS (
    SELECT
        o.*,
        CASE WHEN coordinate_status = 'ok' THEN geom::geography END AS geog,
        CASE WHEN coordinate_status = 'ok' THEN ST_X(geom) END AS longitude,
        CASE WHEN coordinate_status = 'ok' THEN ST_Y(geom) END AS latitude
    FROM object_checks o
),
distances AS (
    SELECT
        c.id AS connection_uuid,
        s.max_distance_m,
        c.building_id,
        COALESCE(b.friendly_name, b.name) AS building_name,
        b.location AS building_municipality,
        b.type AS building_type,
        b.latitude AS building_latitude,
        b.longitude AS building_longitude,
        COALESCE(b.coordinate_status, 'object_missing') AS building_coordinate_status,
        c.distribution_box_id,
        COALESCE(d.friendly_name, d.name) AS distribution_box_name,
        d.location AS distribution_box_municipality,
        d.type AS distribution_box_type,
        d.latitude AS distribution_box_latitude,
        d.longitude AS distribution_box_longitude,
        CASE WHEN c.distribution_box_id IS NULL THEN 'not_assigned'
             ELSE COALESCE(d.coordinate_status, 'object_missing')
        END AS distribution_box_coordinate_status,
        c.transformer_id,
        COALESCE(t.friendly_name, t.name) AS transformer_name,
        t.location AS transformer_municipality,
        t.type AS transformer_type,
        t.latitude AS transformer_latitude,
        t.longitude AS transformer_longitude,
        COALESCE(t.coordinate_status, 'object_missing') AS transformer_coordinate_status,
        ST_Distance(b.geog, d.geog) AS building_distribution_box_m,
        ST_Distance(b.geog, t.geog) AS building_transformer_m,
        ST_Distance(d.geog, t.geog) AS distribution_box_transformer_m,
        c.source_outgoing,
        c.disconnect_point_outgoing,
        c.connection_notes
    FROM service_connections c
    CROSS JOIN settings s
    LEFT JOIN checked_objects b ON b.id = c.building_id
    LEFT JOIN checked_objects d ON d.id = c.distribution_box_id
    LEFT JOIN checked_objects t ON t.id = c.transformer_id
),
flagged AS (
    SELECT
        distances.*,
        GREATEST(building_distribution_box_m, building_transformer_m,
                 distribution_box_transformer_m) AS maximum_distance_m,
        CONCAT_WS('; ',
            CASE WHEN building_distribution_box_m > max_distance_m
                 THEN 'building_distribution_box_over_limit' END,
            CASE WHEN building_transformer_m > max_distance_m
                 THEN 'building_transformer_over_limit' END,
            CASE WHEN distribution_box_transformer_m > max_distance_m
                 THEN 'distribution_box_transformer_over_limit' END,
            CASE WHEN building_coordinate_status <> 'ok'
                 THEN 'building:' || building_coordinate_status END,
            CASE WHEN transformer_coordinate_status <> 'ok'
                 THEN 'transformer:' || transformer_coordinate_status END,
            CASE WHEN distribution_box_coordinate_status NOT IN ('ok', 'not_assigned')
                 THEN 'distribution_box:' || distribution_box_coordinate_status END
        ) AS findings,
        CASE
            WHEN building_distribution_box_m > max_distance_m
             AND building_transformer_m > max_distance_m
             AND distribution_box_transformer_m <= max_distance_m
            THEN 'Check building position or building-to-network assignment first'
            WHEN building_distribution_box_m <= max_distance_m
             AND building_transformer_m > max_distance_m
             AND distribution_box_transformer_m > max_distance_m
            THEN 'Check transformer position or assignment first'
            WHEN building_transformer_m <= max_distance_m
             AND building_distribution_box_m > max_distance_m
             AND distribution_box_transformer_m > max_distance_m
            THEN 'Check distribution box position or assignment first'
            ELSE 'Review coordinate statuses and all available distances'
        END AS review_hint
    FROM distances
)
SELECT *
FROM flagged
WHERE findings <> ''
ORDER BY maximum_distance_m DESC NULLS LAST,
         building_municipality, building_name, connection_uuid;
