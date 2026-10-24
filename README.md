# FRS Energy Data API

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
  Reimports add numbers without deleting previously recorded ones.
- Address and connection details expose `insurance_numbers`. Search responses
  can include them with `fields=insurance_numbers`.
- Run import regressions with `python -m unittest discover -s tests -v`.
  Set `TEST_POSTGRES_URL` only for a dedicated PostGIS test database to run
  the optional PostgreSQL integration suite.
