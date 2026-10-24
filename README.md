# FRS Energy Data API

## Import runtime and progress

Excel uses a read-only workbook reader. Connection imports load existing
connections in batches of up to 1,000 buildings and batch-flush new objects and
merge-mode connections instead of sending a query/flush for every row.
The complete import still uses one transaction: other sessions see changes only
after the final commit. A preview rolls back all changes.

Progress is logged through `uvicorn.error` at INFO level: upload/workbook parsing,
waiting for/acquiring the PostgreSQL import lock, object loading, every 500 rows
or buildings, flushing, and committing. Run Uvicorn with INFO logging enabled.
The lock serializes imports. A client-side cancellation does not guarantee that
the server stopped processing; repeated requests can queue behind the first.
Inspect the last progress message before retrying. These optimizations do not
guarantee completion within a particular proxy or client timeout.

## Excel first disconnect location

The `TK ohne Schaltmöglichkeit` column (`disconnect_point` connection role)
accepts both disconnect points and distribution boxes. Exact names, aliases
and nearest-reference resolution use both types, including during merge updates.
The referenced object's type and ID remain unchanged. Buildings and transformer
stations are not eligible for this role.

The Excel `Erste Trennstelle` column accepts both distribution boxes and
transformer stations. A station such as `SUR-Fruchthof` may be referenced by both
the supply and first disconnect fields using the same object ID. The importer
does not change its type or create a second artificial cabinet. Exact names,
spelling aliases, and nearest-reference selection use the same compatible types.
Buildings remain excluded; supply and unswitched-terminal roles retain their
transformer and disconnect-point type requirements respectively.

After deploying this change, reimport Excel using the default merge mode to retry
previously skipped rows. No database reset or additional migration is required.
Genuinely missing references and ambiguous geographic matches are still skipped.

## GPS identities and nearest Excel references

GPS imports treat different nonempty `ckw_id` values as separate assets, even
when their names, types and municipalities are identical. Reimporting the same
ID updates that asset's coordinates without creating another object. A unique
same-name legacy object without a CKW ID may still be adopted. Without an ID,
name matching remains ambiguous when multiple objects exist.

For Excel/connection imports, multiple same-name candidates of the required
network type are resolved by their distance to the existing building point.
Exact normalized names take precedence over spelling aliases. Within the chosen
candidate set, distance takes precedence over municipality. Distances are
approximate great-circle metres using the haversine formula and EPSG:4326 points.
The building and every candidate must have valid coordinates; a distance gap of
one metre or less between the two closest candidates is rejected as ambiguous.
Unique reference matches retain the existing matching behavior.

The `nearest_reference_matched` diagnostic records the chosen internal ID,
CKW ID, distance, and distances to all candidates. There is no automatic 1 km
cutoff. A nearest match is a geometric heuristic, not proof of the electrical
connection. Incorrect building coordinates can produce an incorrect selection.
Import GPS assets (including building coordinates) first, then reimport Excel.
Use the existing replacement preview/apply mode when stale connections prevent
an ordinary merge; nearest matching does not by itself delete old connections.

## Bounding-box coordinate audit

Run [scripts/check_objects_outside_bbox.sql](scripts/check_objects_outside_bbox.sql)
to list stored objects outside the configured Swiss LV95 (EPSG:2056) rectangle.
Edit the two arrays `location_min` and `location_max` at the top of the script.
Defaults are `(2647019, 1221480)` and `(2654711, 1230154)`. Stored WGS84 points
are transformed to LV95 for the comparison; boundary points are inside.

The query includes all object types, including objects without a CKW ID, but
returns only points confirmed outside the box. Missing or invalid coordinates
are excluded by default; the alternative documented filter includes their
diagnostic statuses. Uncomment the CKW-ID filter to inspect only objects with a
recorded `ckw_id`. Results include IDs, names, municipalities, types, CKW IDs,
WGS84 coordinates, and LV95 eastings/northings for comparison with the bounds.
This is a read-only audit of the local database, not a download from the CKW API.
It does not change coordinates or connection assignments.

## Connection coordinate audit

Run [scripts/check_connection_distances.sql](scripts/check_connection_distances.sql)
against the PostgreSQL/PostGIS database to find suspicious connection locations.
This read-only query checks all three pairs: building/distribution box,
building/transformer, and distribution box/transformer. Change `max_distance_m`
(default 1,000 metres) in the first CTE to adjust the threshold. Exactly 1,000 m
is within the limit. Distances use WGS84 geography, not degrees or pipe lengths.

Results include object IDs, names, municipalities, coordinate statuses, valid
coordinates, pairwise distances in metres, and review hints. Missing, empty,
out-of-range, or otherwise invalid geometries are reported instead of silently
excluded. An unassigned optional distribution box is not itself an error; the
building/transformer pair is still checked. Sort order puts the largest known
distance first and records with no calculable distance last.

A distance violation flags a candidate for review, not proof that a particular
coordinate is wrong: the connection assignment may be wrong, or the real service
area may exceed the threshold. Review hints only identify the isolated point
relative to the other two. Objects without service connections and optional
disconnect points are outside this audit. Existing duplicate connections remain
separate results, identifiable by their connection UUIDs. No records are changed.

Backend service for importing and searching electricity grid object/location data.

## Features

- Import object and connection data from Excel and JSON.
- Import GPS points for grid objects.
- Resolve repeated multiline grid references and unique `str.`/`strasse` aliases.
- Synchronize equivalent connection duplicates while preserving their IDs.
- Search addresses with tokenized/fuzzy-friendly matching.
- Retrieve one address by UUID.
- Find nearby addresses by coordinate and radius.

## Data model (high level)

### `objects`
Stores physical entities (building, transformer, distribution box, disconnect point), optional geometry,
and `insurance_numbers` (the building-related `Assek. Nr.` values from Excel).
These identifiers preserve leading zeroes, may be shared across addresses, and
are separate from `ckw_id`. Multiple numbers per address are supported.

### `service_connections`
Stores only connection-related information between referenced objects:

- `building_id` (FK to `objects`)
- `transformer_id` (FK to `objects`)
- `distribution_box_id` (FK to `objects`, optional)
- `disconnect_point_id` (FK to `objects`, optional)
- `disconnect_point_outgoing` (JSONB)
- `source_outgoing` (JSONB)
- `connection_notes` (JSONB)

## API overview

### Import

- `POST /import/excel`
- `POST /import/service-connections`
- `POST /import/gps`
- `POST /import/gps-legacy`

### Search

- `GET /search/?q=<query>&fields=address&fields=location&fields=uuid`
- `GET /search/address?q=<query>&fields=address&fields=location&fields=uuid`
- `GET /search/address/{uuid}`
- `GET /search/connection?q=<query>&fields=address&fields=location&fields=connection_uuid`
- `GET /search/connection/{uuid}`
- `GET /search/nearby?lat=<latitude>&lon=<longitude>&radius=500`

## Local run

1. Install dependencies:

```bash
pip install -r requirements.txt
```

SQLAlchemy is installed with its `asyncio` extra to include `greenlet`, which
the asynchronous database layer requires. After changing dependencies, rebuild
the Docker image and recreate the container; restarting an existing container
does not install updated requirements.

2. Configure PostgreSQL/PostGIS connection in `database.py` if needed.

3. Start app (example):

```bash
uvicorn main:app --reload
```

## Replacing stale Excel connections

The default `POST /import/excel` merges data without deleting connections.
To reconcile the complete connection set for each building in an Excel upload:

1. Preview: `POST /import/excel?mode=replace_buildings&dry_run=true`.
2. Inspect `deleted`, `removed_connections`, `skipped`, and `warnings_by_reason`.
3. Apply the same upload: `POST /import/excel?mode=replace_buildings&dry_run=false`.

Send the workbook as the multipart `file` field. Replacement requires the original
nine-column `Modified` layout and all rows for each included building. It retains
one ID per matching path, combines notes from repeated source rows, deletes stale
connections, and replaces the building's insurance-number list. Blank optional
references clear old references in this mode. New paths receive new IDs.

A missing/ambiguous reference or invalid row blocks connection replacement for
that building. Stale connections with issue reports also block replacement.
Buildings absent from the upload, objects with unknown municipality, and grid
objects are not deleted. This is per-building replacement, not a database reset.
The preview rolls back all writes; its counters describe planned changes.
`consolidated` counts input rows combined into one connection. Save the response
if you need the old connection values listed in `removed_connections`.

## Notes

- Import responses count `created`, `updated`, and `unchanged` entries in
  `imported_count`. An unchanged entry was processed successfully.
  `warnings_by_reason` summarizes diagnostics. Missing or ambiguous references
  still require source data or explicit correction; they are not guessed.
- Equivalent existing connections (same full path, outgoing arrays and notes)
  can be synchronized together. Conflicting duplicates remain skipped.

- Import matching, update rules, limitations and regression tests are documented in
  [docs/import-behavior.md](docs/import-behavior.md).

- Before deploying this version to an existing database, apply
  [migrations/001_object_insurance_numbers.sql](migrations/001_object_insurance_numbers.sql).
  Startup `create_all()` does not add columns to existing tables. The migration
  only adds the nullable column; reimport the Excel file to populate it.
- Excel imports preserve multiline note blocks and collect multiple insurance
  numbers for repeated addresses. Ambiguous positions are reported and skipped.
  Merge imports add numbers without deleting previously recorded ones.
- Address and connection details expose `insurance_numbers`. Search responses
  can include them with `fields=insurance_numbers`.
- Run import regressions with `python -m unittest discover -s tests -v`.
  Set `TEST_POSTGRES_URL` only for a dedicated PostGIS test database to run
  the optional PostgreSQL integration suite.

## Recommended import order and historical repair

1. Import authoritative CKW infrastructure with `POST /import/gps` and a JSON
   body `{"points": [...]}`. Supply `ckw_id` and an explicit type: `transformer`,
   `distribution_box`, or `disconnect_point`.
2. Import the Excel connection lists. Buildings are separate objects, even when
   their address equals a grid object's name (for example Schlottermilch 10).
   Each network reference must match its role's compatible types (the first
   disconnect location also accepts a transformer station). Missing or ambiguous
   references skip the row and appear in the import warnings.
3. Let the external geocoding application fill coordinates only for objects with
   `type = 'building'` and missing geometry, using the building UUID to update
   the intended record. Do not identify an update by address alone. This repository
   does not modify that external application's behavior.

For historical CKW objects incorrectly typed as buildings, back up the database
and first submit the authoritative GPS file to:

`POST /import/gps?repair_buildings=true&dry_run=true`

Review `repairs`, `skipped`, and `warnings`. The preview rolls back all writes.
Apply the same file with `POST /import/gps?repair_buildings=true`.
This explicit repair requires a unique matching CKW ID and an explicit grid type.
It retains the infrastructure UUID, CKW ID and network references, restores its
GPS type, and moves building connections to a separate building. A unique existing
building at the same normalized address and location is reused; otherwise one is
created. Insurance numbers move to the building. Newly created buildings have no
coordinates or CKW ID; existing building coordinates are preserved. Connection
UUIDs and issue-report links remain intact. Conflicting network roles or ambiguous
replacement buildings are skipped for review. Repeating the repaired import does
not create another building.

Normal GPS imports still reject type conflicts. Both normal and repair GPS imports
support `dry_run=true`. After repair, reimport the connection lists to resolve
previously missing references and review all warnings. Do not clear the database
or revert the import code to recover these objects.

## Nearby type filter

`GET /search/nearby` accepts an optional repeated `types` query parameter:

```text
/search/nearby?lat=47.13&lon=8.00&radius=500&types=distribution_box&types=disconnect_point
```

Allowed values are `building`, `transformer`, `distribution_box`, and
`disconnect_point`. Omit `types` to include all four types. Unknown values return
HTTP 422. Filtering happens in the database before the nearest-result limit
(default 100, maximum 500). Each result includes its `type`. Objects without
coordinates cannot appear in a radius search. Disconnect points were already
eligible for unfiltered nearby searches; no type was previously excluded.
## Reference names and GPS display formatting

Excel reference matching ignores whitespace and accepts a terminal house number
in parentheses: `Birkenweg(5)`, `Birkenweg (5)` and `Birkenweg 5` can resolve to
the same infrastructure object. A single letter suffix is supported (`5A`).
Other parenthetical text, number ranges and different house numbers remain
distinct. Exact matches take precedence; ambiguous aliases still require the
existing geographic resolution rules and are skipped if they cannot be resolved.
This also works with previously imported objects without a GPS reimport.

GPS imports format `friendly_name` as `Birkenweg (5)` or `Birkenweg 5A` on
creation and update. Matching identities (`name`, CKW ID) are not changed by
display formatting. An existing readable display name is preserved where possible.
These aliases only address formatting differences; missing infrastructure,
incompatible types and unresolved ambiguous references can still be skipped.
