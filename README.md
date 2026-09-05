# FRS Energy Data API

Backend service for importing and searching electricity grid object/location data.

## Features

- Import object and connection data from Excel and JSON.
- Import GPS points for grid objects.
- Search addresses with tokenized/fuzzy-friendly matching.
- Retrieve one address by UUID.
- Find nearby addresses by coordinate and radius.

## Data model (high level)

### `objects`
Stores physical entities (building, transformer, distribution box, disconnect point) and optional geometry.

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

2. Configure PostgreSQL/PostGIS connection in `database.py` if needed.

3. Start app (example):

```bash
uvicorn main:app --reload
```

## Notes

- Import matching, update rules, limitations and regression tests are documented in
  [docs/import-behavior.md](docs/import-behavior.md).

- This repository currently contains schema-level refactors without database migration scripts.
- If you already have deployed tables, add a migration before rolling out.
