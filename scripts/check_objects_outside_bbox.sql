-- Read-only PostgreSQL/PostGIS audit. Returns every stored object outside the
-- specified Swiss LV95 bounding box. Uncheckable positions are excluded by default.
-- Includes all object types and objects without a CKW ID. No rows are changed.
-- Boundary points count as inside. Coordinates are transformed, never relabelled.

WITH parameters AS (
    SELECT
        ARRAY[2647019.0, 1221480.0]::double precision[] AS location_min, -- minX, minY
        ARRAY[2654711.0, 1230154.0]::double precision[] AS location_max  -- maxX, maxY
),
bounds AS (
    SELECT
        location_min[1] <= location_max[1]
        AND location_min[2] <= location_max[2] AS bounds_valid,
        ST_MakeEnvelope(location_min[1], location_min[2],
                        location_max[1], location_max[2], 2056) AS bbox
    FROM parameters
),
checked AS (
    SELECT o.*,
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
    -- To inspect only objects with an assigned CKW ID, uncomment:
    -- WHERE NULLIF(BTRIM(o.ckw_id), '') IS NOT NULL
),
classified AS (
    SELECT o.*, b.bbox,
        CASE
            WHEN NOT b.bounds_valid THEN 'invalid_bbox'
            WHEN o.coordinate_status <> 'ok' THEN o.coordinate_status
            -- Compare in LV95 so the supplied rectangular limits remain exact.
            WHEN ST_Covers(b.bbox, ST_Transform(o.geom, 2056)) THEN 'inside_bbox'
            ELSE 'outside_bbox'
        END AS bbox_status
    FROM checked o CROSS JOIN bounds b
)
SELECT
    id AS object_uuid,
    name,
    friendly_name,
    location AS municipality,
    type,
    ckw_id,
    bbox_status,
    coordinate_status,
    CASE WHEN coordinate_status = 'ok' THEN ST_X(geom) END AS longitude,
    CASE WHEN coordinate_status = 'ok' THEN ST_Y(geom) END AS latitude,
    CASE WHEN coordinate_status = 'ok' THEN ST_X(ST_Transform(geom, 2056)) END AS lv95_easting,
    CASE WHEN coordinate_status = 'ok' THEN ST_Y(ST_Transform(geom, 2056)) END AS lv95_northing,
    description
FROM classified
WHERE bbox_status = 'outside_bbox'
-- To include missing/invalid coordinates as separate diagnostic statuses, use:
-- WHERE bbox_status <> 'inside_bbox'
ORDER BY bbox_status, municipality NULLS LAST, name, id;
