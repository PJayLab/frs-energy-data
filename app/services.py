from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2 import WKTElement

from app.database import AsyncSessionLocal, engine
from app.models import Object, ServiceConnection, ObjectType
from app.schemas import ImportData, GPSImportData
from app.import_helpers import (
    AmbiguousMatch, clean_text, normalize_object_name, split_positional,
    split_compact, normalize_array_for_comparison, find_existing_object,
    upsert_object, build_connection_signature, find_existing_service_connection,
    upsert_service_connection, import_transaction, new_report, load_objects,
)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Object.metadata.create_all)
        await conn.run_sync(ServiceConnection.metadata.create_all)


TECH_HINTS = ['PVA', 'kVA', 'Ladestation']
split_cell = split_compact
split_and_clean = split_compact


def is_technical(texts):
    return any(hint in ' '.join(texts) for hint in TECH_HINTS)


def assign_notes(objects, notes):
    """Position first; retain overflow heuristics for inconsistent sheets."""
    if len(objects) == 1:
        return [split_compact(notes)], []
    if len(notes) <= len(objects):
        return [[notes[i]] if i < len(notes) and notes[i] else [] for i in range(len(objects))], []
    warning = dict(field='connection_notes', reason='ambiguous_note_positions', values=notes)
    if is_technical(notes):
        return [split_compact(notes) for _ in objects], [warning]
    return [[notes[i]] if notes[i] else [] for i in range(len(objects))], [warning]


def normalize_row(row):
    row = list(row) + [None] * max(0, 9 - len(row))
    objects = split_positional(row[1])
    insurance = split_positional(row[2])
    notes, warnings = assign_notes(objects, split_positional(row[3]))
    sources = split_compact(row[7])
    return [dict(
        municipality=clean_text(row[0]), object=name,
        insurance_number=insurance[i] if i < len(insurance) else None,
        connection_notes=notes[i], unswitched_terminal=clean_text(row[4]),
        first_disconnect_point=clean_text(row[5]),
        disconnect_point_outgoing=split_compact(row[6]),
        source_name=sources[0] if sources else None, source_names=sources,
        source_outgoing=split_compact(row[8]), _warnings=warnings,
    ) for i, name in enumerate(objects) if name]


def entry_value(entry, key, alias=None):
    # Explicit [] takes precedence over an alias; None is unknown, not deletion.
    return entry[key] if key in entry else entry.get(alias)


def source_values(entry):
    primary = split_compact(entry_value(entry, 'source_name', 'speisung'))
    sources = split_compact(entry.get('source_names'))
    # source_name remains the public editable field. source_names supplements it.
    if len(primary) > 1:
        return primary
    return sources if sources and (not primary or primary[0] == sources[0]) else primary


async def _import_connections(raw_entries, session, objects, report):
    prepared = defaultdict(list)
    for row_number, entry in enumerate(raw_entries, 1):
        display = clean_text(entry.get('object') or entry.get('objekt'))
        context = dict(object=display, row=entry.get('_excel_row', row_number))
        report['warnings'].extend({**w, **context} for w in entry.get('_warnings', []))
        if not display:
            report['warnings'].append(dict(**context, field='building', reason='empty_object_name'))
            report['skipped'] += 1
            continue
        location = clean_text(entry.get('municipality') or entry.get('gemeinde'))
        sources = source_values(entry)
        if len(sources) > 1:
            report['warnings'].append(dict(**context, field='transformer', reason='multiple_sources_single_transformer', values=sources, selected=sources[0]))
        references = {}
        failed = False
        for field, name in (
            ('disconnect_point', entry_value(entry, 'unswitched_terminal', 'tk_ohne_schalt')),
            ('distribution_box', entry_value(entry, 'first_disconnect_point', 'erste_trennstelle')),
            ('transformer', sources[0] if sources else None),
        ):
            explicit_clear = name == ''
            name = clean_text(name)
            if not name and field != 'transformer':
                if explicit_clear:
                    references[field + '_id'] = None
                continue
            try:
                found = find_existing_object(objects, name, location) if name else None
                # Grid assets can feed buildings across municipal boundaries.
                if not found and name:
                    found = find_existing_object(objects, name)
                reason = 'object_not_found'
            except AmbiguousMatch:
                found, reason = None, 'ambiguous_object_reference'
            if found:
                references[field + '_id'] = found.id
            else:
                report['warnings'].append(dict(**context, field=field, missing_reference=name, reason=reason))
                failed = True
        if failed:
            # An unresolved explicit reference must not partially change a connection.
            report['skipped'] += 1
            continue
        try:
            building, status = await upsert_object(session, objects, name=display, location=location, ckw_id=entry.get('ckw_id'), type=ObjectType.building)
        except AmbiguousMatch as exc:
            report['warnings'].append(dict(**context, field='building', reason='ambiguous_object', detail=str(exc)))
            report['skipped'] += 1
            continue
        report['objects'][status] += 1
        values = dict(building_id=building.id, **references)
        for field, alias in (('source_outgoing', 'abgang_speisung'), ('disconnect_point_outgoing', 'abgang_trennstelle'), ('connection_notes', 'bemerkungen')):
            value = entry_value(entry, field, alias)
            if value is not None:
                if isinstance(value, (list, tuple)) and any(v is None for v in value):
                    report['warnings'].append(dict(**context, field=field, reason='null_array_item_ignored'))
                    if not split_compact(value):
                        continue
                values[field] = split_compact(value)
        prepared[building.id].append((values, context))

    for building_id, rows in prepared.items():
        candidates = list((await session.scalars(select(ServiceConnection).where(ServiceConnection.building_id == building_id))).all())
        signatures = {build_connection_signature(values) for values, _ in rows}
        seen = set()
        for values, context in rows:
            signature = build_connection_signature(values)
            if signature in seen:
                report['warnings'].append(dict(**context, field='connection', reason='duplicate_in_batch'))
            seen.add(signature)
            try:
                connection, status = await upsert_service_connection(session, candidates, values, signatures)
            except AmbiguousMatch as exc:
                report['warnings'].append(dict(**context, field='connection', reason='ambiguous_connection', detail=str(exc)))
                report['skipped'] += 1
                continue
            report[status] += 1
            report['imported'].append(connection)


async def import_service_connections(raw_entries: list[dict], session: AsyncSession):
    report = new_report()
    async with import_transaction(session):
        await _import_connections(raw_entries, session, await load_objects(session), report)
    return report


async def import_gps_objects(import_data: GPSImportData, session: AsyncSession, *, return_report=False):
    report = new_report()
    async with import_transaction(session):
        objects = await load_objects(session)
        for point in import_data.points:
            obj_type = point.type
            if obj_type:
                try:
                    obj_type = ObjectType(obj_type)
                except ValueError:
                    report['warnings'].append(dict(object=point.name, field='type', reason='invalid_object_type', value=obj_type))
                    obj_type = None
            try:
                existing = find_existing_object(objects, point.name, point.location, point.ckw_id)
                obj, status = await upsert_object(
                    session, objects, name=point.name, location=point.location, ckw_id=point.ckw_id,
                    type=obj_type or (existing.type if existing else ObjectType.distribution_box),
                    geom=WKTElement(f'POINT({point.lon} {point.lat})', srid=4326),
                )
            except AmbiguousMatch as exc:
                report['warnings'].append(dict(object=point.name, field='object', reason='ambiguous_object', detail=str(exc)))
                report['skipped'] += 1
                continue
            report[status] += 1
            report['imported'].append(obj)
    return report if return_report else report['imported']


async def import_to_db(import_data: ImportData):
    """Legacy endpoint still creates named references, using shared upserts."""
    report = new_report()
    async with AsyncSessionLocal() as session:
        async with import_transaction(session):
            objects = await load_objects(session)
            # raw_entries retain municipality and missing-vs-empty semantics that
            # the historical validator's name-only object map would discard.
            for entry in import_data.raw_entries:
                for field, alias, obj_type in (
                    ('unswitched_terminal', 'tk_ohne_schalt', ObjectType.disconnect_point),
                    ('first_disconnect_point', 'erste_trennstelle', ObjectType.distribution_box),
                    ('source_name', 'speisung', ObjectType.transformer),
                ):
                    names = source_values(entry) if field == 'source_name' else split_compact(entry_value(entry, field, alias))
                    for name in names:
                        try:
                            _, status = await upsert_object(session, objects, name=name, type=obj_type)
                            report['objects'][status] += 1
                        except AmbiguousMatch as exc:
                            report['warnings'].append(dict(object=name, field=field, reason='ambiguous_object', detail=str(exc)))
            await _import_connections(import_data.raw_entries, session, objects, report)
    return report
