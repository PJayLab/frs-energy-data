# Import matching and update rules

The existing Object / ServiceConnection model is unchanged. Notes remain lists
of strings in JSONB; outgoing values remain arrays. No automatic data cleanup or
schema migration is performed.

## Causes addressed

Previously every connection and every legacy import created new records. Excel
matched compact names globally, while GPS matched literal names. Empty lines in
notes and insurance numbers were removed before matching object positions.
Missing optional references were not reported, and missing transformers silently
discarded connections.

## Object identity

All three import paths use `normalize_object_name`: remove whitespace (including
nonbreaking spaces), retaining case, accents and punctuation. `friendly_name`
keeps readable spacing. A compact GPS name does not replace a formatted display
name when both normalize to the same identity.

1. A nonempty `ckw_id` matches first, even after a rename.
2. Otherwise match normalized name and trimmed municipality/location.
3. With a municipality, a unique historical object without location may be
   adopted if there is no exact municipal match.
4. Without municipality, only a unique name match is accepted.

Conflicting asset IDs and multiple candidates produce warnings and skips rather
than arbitrary matches or new duplicates. Incoming nonempty object attributes
update existing values; missing values preserve them. GPS accepts optional
`location`. Missing/invalid GPS type preserves an existing type, defaulting to
`distribution_box` only for a new object; invalid types are reported.

Referenced grid objects first use the same municipality matching. A unique
global fallback is allowed because a grid asset can feed another municipality.
Names remain case sensitive. Missing municipality and historical missing
locations can still make identity fundamentally uncertain.

Objects are indexed in memory once per import by normalized name and asset ID;
new and renamed objects immediately update that index.

## Connection identity and Excel edits

Signatures contain building, transformer, distribution box, disconnect point and
both outgoing arrays. Arrays are trimmed and stripped of empty/null elements,
but **order is preserved** because outgoing positions can describe corresponding
paths. Insurance numbers are not matching keys.

- First match the connection signature. Unspecified fields are unknown and must
  not contradict an existing value; multiple possible matches are ambiguous.
- If the building has one existing connection and the batch contains one distinct
  incoming signature for it, update the existing connection. This handles e.g.
  changing outgoing `4` to `5`, including updates to the referenced objects.
- Multiple distinct incoming connections are retained. A new signature alongside
  existing connections is accepted only when all existing signatures also occur
  in the batch, so existing connections cannot silently be reassigned.
- Otherwise skip with `ambiguous_connection`. Never delete existing connections.
- Duplicate signatures in a batch update the same record; last supplied notes win.

There is no source-row ID or provenance field. Therefore a lone new connection
cannot be distinguished perfectly from an edit to the sole existing connection.
Likewise edits among multiple existing connections require clarification or an
exact matching signature. Pre-existing duplicates are reported, not merged.

Supplied connection arrays/notes synchronize to the current import, including
explicit `[]` to clear them. Missing keys and `None` preserve existing values.
An all-null array is treated as unknown; mixed arrays discard nulls with a warning.
For optional foreign keys, missing/None references preserve existing values;
an explicit empty string in JSON clears them. Blank Excel reference cells parse
as None, so removing those references requires an explicit JSON edit. An explicit
unresolved reference skips the whole connection and reports every missing field.
Transformer is always required.

## Excel positions and remaining schema limits

Object, insurance and note cells preserve leading/interior empty positions;
only trailing empty positions are trimmed. Blank object positions are omitted
only after assigning corresponding insurance and note positions.

One object's notes stay together. Up to one note position per object is assigned
positionally, padding missing trailing notes with empty lists. Only surplus notes
use the previous fallback: technical notes are replicated; ordinary surplus
notes are truncated to the object positions. Both overflow cases warn and include
the original notes in the report, making the legacy ambiguity visible.

`source_name` retains its existing role. `source_names` carries all Excel source
names through normalization; the first source is selected because the schema has
only one transformer foreign key. Multiple sources are reported with all names
and the selected source. Outgoing arrays remain intact. A later edit to the public
`source_name` takes precedence over previously normalized source metadata.

Insurance numbers are positionally parsed but not persisted, as before. If later
required, a nullable **nonunique** `Object.insurance_number` text column is a
minimal extension (preserving leading zeroes). It must not become a global or
municipal unique key. Numeric Excel cells cannot recover discarded leading zeroes
from values alone; source cells should be stored as text.

## Transactions and API reports

Each entry point owns one commit or rollback. Unexpected technical errors propagate
and roll back the whole import. An existing authentication read transaction is
supported; callers must not pass sessions containing unrelated pending writes.
PostgreSQL transaction advisory lock `724183901` serializes the import paths,
including GPS/legacy, to prevent concurrent check-then-insert races. Other writers
that do not use this lock are outside this guarantee.

Responses retain legacy fields and add `created`, `updated`, `unchanged`, `skipped`,
`warnings`, `warnings_count`, and `errors`. Counts are per input operation, not
unique IDs: a duplicate row can increment `updated` after the first increments
`created`. Connection imports report object operations separately in `objects`;
the legacy endpoint uses `object_stats` to retain its existing numeric `objects`.
GPS counters refer to objects. Expected data problems are warnings; technical
errors raise instead of returning a misleading successful report. Excel warnings
include original worksheet row numbers.

## Constraints reviewed

Read-only inspection of the configured local PostgreSQL database on 2026-09-05:
16,044 objects and 3,902 connections. Both tables have primary keys; connections
have the four foreign keys. The geometry has a GiST index. There is no unique
constraint on object name, asset ID or connection signature. No duplicate groups
of trimmed, nonempty asset IDs were found. Deployed databases may differ.

If the business confirms asset numbers are globally unique, recommend the
following **optional** PostgreSQL index, after checking the target database:

```sql
SELECT btrim(ckw_id) AS asset_id, count(*)
FROM objects
WHERE nullif(btrim(ckw_id), '') IS NOT NULL
GROUP BY btrim(ckw_id)
HAVING count(*) > 1;

CREATE UNIQUE INDEX uq_objects_ckw_id_nonempty
ON objects (btrim(ckw_id))
WHERE nullif(btrim(ckw_id), '') IS NOT NULL;
```

The index creation itself fails safely if duplicates exist or race with the
preflight; it deletes no data. It is deliberately not applied automatically:
asset uniqueness must hold in every target deployment. A connection unique index
alone would not solve edited outgoing values or ambiguous existing connections.

## Verification

Run without additional test dependencies beyond `requirements.txt`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

SQLite tests use real async sessions with test-only substitutions for JSONB and
geometry storage. For the same regressions on real PostgreSQL/PostGIS, set
`TEST_POSTGRES_URL` to a test database connection URL before running the command.
That database must already provide PostGIS and the `object_type` enum. Tests create
random `import_test_<uuid>` schemas and drop only those schemas afterward; they
never import into application tables. A PostgreSQL-only test checks simultaneous
imports. Do not run against a database where transient test schemas are unwanted.

Coverage includes all eight requested cases, persisted positional notes, blank
object/insurance positions, technical/ordinary fallback warnings, CRLF/NBSP/zero,
batch duplicates, real workbook route reimport, multiple connections and ambiguous
edits, missing references, optional reference updates/preservation/clearing,
null/normalized arrays, legacy reimports, historical names/locations, conflicting
and duplicate asset IDs, GPS preservation/renaming, multiple-source warnings,
atomic rollback, and concurrent imports.
