from collections import defaultdict
import logging

logger = logging.getLogger('uvicorn.error')

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2 import WKTElement

from app.database import AsyncSessionLocal, engine
from app.models import Object, ServiceConnection, ObjectType
from app.schemas import ImportData, GPSImportData
from app.connection_sync import replace_building_connections
from app.import_helpers import (
    AmbiguousMatch, clean_text, normalize_object_name, split_positional,
    split_compact, normalize_array_for_comparison, find_existing_object,
    upsert_object, build_connection_signature, find_existing_service_connection,
    upsert_service_connection, import_transaction, new_report, load_objects,
    unique_reference_names, find_reference_object,
)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Object.metadata.create_all)
        await conn.run_sync(ServiceConnection.metadata.create_all)


split_cell = split_compact
split_and_clean = split_compact


def assign_notes(objects, notes):
    """Blank object lines continue the preceding object's note block."""
    assigned = [[] for _ in objects]
    positions = [i for i, name in enumerate(objects) if name]
    if len(positions) == 1:
        assigned[positions[0]] = split_compact(notes)
        return assigned, []
    previous = None
    warnings = []
    for i, note in enumerate(notes):
        if i < len(objects) and objects[i]:
            previous = i
        if not note:
            continue
        if i < len(objects) and previous is not None:
            assigned[previous].append(note)
        elif i >= len(objects) and note.startswith('Parallel Strang '):
            for position in positions:
                assigned[position].append(note)
            warnings.append(dict(field='connection_notes', reason='row_wide_parallel_note', values=[note]))
        else:
            warnings.append(dict(field='connection_notes', reason='ambiguous_note_positions', values=notes))
    return assigned, warnings


def normalize_row(row):
    row = list(row) + [None] * max(0, 9 - len(row))
    # Retain trailing layout lines, which may hold notes for the last object.
    objects = [line.strip() for line in str(row[1] or '').replace('\xa0', ' ').replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    insurance = split_positional(row[2])
    notes, warnings = assign_notes(objects, split_positional(row[3]))
    if any(number and (i >= len(objects) or not objects[i]) for i, number in enumerate(insurance)):
        warnings.append(dict(field='insurance_numbers', reason='unassigned_insurance_number', values=insurance))
    invalid = any(w['reason'] in ('ambiguous_note_positions', 'unassigned_insurance_number') for w in warnings)
    sources = split_compact(row[7])
    entries = [dict(
        municipality=clean_text(row[0]), object=name,
        insurance_number=insurance[i] if i < len(insurance) else None,
        insurance_numbers=[insurance[i]] if i < len(insurance) and insurance[i] else [],
        connection_notes=notes[i], unswitched_terminal=clean_text(row[4]),
        first_disconnect_point=clean_text(row[5]),
        disconnect_point_outgoing=split_compact(row[6]),
        source_name=sources[0] if sources else None, source_names=sources,
        source_outgoing=split_compact(row[8]), _warnings=warnings, _skip_import=invalid,
    ) for i, name in enumerate(objects) if name]
    # Several insured structures can share an address and connection.
    grouped = {}
    for entry in entries:
        key = normalize_object_name(entry['object'])
        if key not in grouped:
            grouped[key] = entry
        else:
            target = grouped[key]
            for field in ('insurance_numbers', 'connection_notes'):
                target[field] = list(dict.fromkeys(target[field] + entry[field]))
            target['insurance_number'] = target['insurance_numbers'][0] if len(target['insurance_numbers']) == 1 else None
    if not grouped and any(clean_text(value) for value in row):
        return [dict(object=None, _warnings=warnings + [dict(field='building', reason='empty_object_name')], _skip_import=True)]
    return list(grouped.values())


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


async def _import_connections(raw_entries, session, objects, report, *, replace_existing=False):
    prepared = defaultdict(list)
    blocked = set()
    buildings = {}
    insurance_by_building = defaultdict(list)
    object_statuses = defaultdict(list)
    name_locations = defaultdict(set)
    for entry in raw_entries:
        name_locations[normalize_object_name(entry.get('object') or entry.get('objekt'))].add(
            clean_text(entry.get('municipality') or entry.get('gemeinde')))
    for row_number, entry in enumerate(raw_entries, 1):
        if row_number == 1 or row_number % 500 == 0:
            logger.info('Import: preparing connection row %s/%s', row_number, len(raw_entries))
        display = clean_text(entry.get('object') or entry.get('objekt'))
        location = clean_text(entry.get('municipality') or entry.get('gemeinde'))
        scope = (location, normalize_object_name(display))
        context = dict(object=display, location=location, row=entry.get('_excel_row', row_number))
        report['warnings'].extend({**w, **context} for w in entry.get('_warnings', []))
        if entry.get('_skip_import'):
            blocked.add(scope)
            report['skipped'] += 1
            continue
        if not display:
            report['warnings'].append(dict(**context, field='building', reason='empty_object_name'))
            report['skipped'] += 1
            continue
        if not location:
            report['warnings'].append(dict(**context, field='building', reason='missing_municipality'))
            report['skipped'] += 1
            blocked.add(scope)
            continue
        sources = unique_reference_names(source_values(entry))
        if len(sources) > 1:
            report['warnings'].append(dict(**context, field='transformer', reason='multiple_sources_single_transformer', values=sources, selected=sources[0]))
            if replace_existing:
                blocked.add(scope)
                report['skipped'] += 1
                continue
        references = {}
        failed = False
        try:
            reference_building = find_existing_object(objects, display, location, entry.get('ckw_id'),
                expected_type=ObjectType.building,
                allow_locationless=not replace_existing and len(name_locations[scope[1]]) == 1)
        except AmbiguousMatch as exc:
            report['warnings'].append(dict(**context, field='building', reason='ambiguous_object', detail=str(exc)))
            blocked.add(scope)
            report['skipped'] += 1
            continue
        for field, name in (
            ('disconnect_point', entry_value(entry, 'unswitched_terminal', 'tk_ohne_schalt')),
            ('distribution_box', entry_value(entry, 'first_disconnect_point', 'erste_trennstelle')),
            ('transformer', sources[0] if sources else None),
        ):
            explicit_clear = name == ''
            names = unique_reference_names(name)
            if len(names) > 1:
                report['warnings'].append(dict(**context, field=field,
                    reason='multiple_distinct_references', values=names))
                failed = True
                continue
            name = names[0] if names else None
            if not name and field != 'transformer':
                if explicit_clear or replace_existing:
                    references[field + '_id'] = None
                continue
            try:
                diagnostics = []
                found, alias_used = find_reference_object(objects, name, location, expected_type=field,
                    building=reference_building, diagnostics=diagnostics, use_nearest=True) if name else (None, False)
                report['warnings'].extend({**w, **context, 'field': field, 'source_name': name} for w in diagnostics)
                reason = 'object_not_found'
            except AmbiguousMatch as exc:
                found, reason = None, 'ambiguous_object_reference'
                report['warnings'].append(dict(**context, field=field, reason=reason,
                    missing_reference=name, detail=str(exc)))
                failed = True
                continue
            if found:
                references[field + '_id'] = found.id
                if alias_used:
                    report['warnings'].append(dict(**context, field=field, reason='reference_alias_matched',
                        source_name=name, matched_name=found.name, matched_id=found.id))
            else:
                report['warnings'].append(dict(**context, field=field, missing_reference=name, reason=reason))
                failed = True
        if failed:
            blocked.add(scope)
            # An unresolved explicit reference must not partially change a connection.
            report['skipped'] += 1
            continue
        try:
            building, status = await upsert_object(session, objects, name=display, location=location,
                ckw_id=entry.get('ckw_id'), type=ObjectType.building,
                defer_flush=True,
                allow_locationless=not replace_existing and len(name_locations[scope[1]]) == 1)
        except AmbiguousMatch as exc:
            blocked.add(scope)
            report['warnings'].append(dict(**context, field='building', reason='ambiguous_object', detail=str(exc)))
            report['skipped'] += 1
            continue
        report['objects'][status] += 1
        object_statuses[building.id].append(status)
        numbers = split_compact(entry_value(entry, 'insurance_numbers', 'insurance_number'))
        buildings[building.id] = building
        insurance_by_building[building.id].extend(numbers)
        if numbers and not replace_existing:
            combined = list(dict.fromkeys((building.insurance_numbers or []) + numbers))
            if combined != building.insurance_numbers:
                building.insurance_numbers = combined
                if status == 'unchanged':
                    report['objects']['unchanged'] -= 1
                    report['objects']['updated'] += 1
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

    logger.info('Import: flushing prepared objects for %s buildings', len(prepared))
    await session.flush()
    existing_connections = defaultdict(list)
    building_ids = list(prepared)
    logger.info('Import: loading existing connections in batches')
    for offset in range(0, len(building_ids), 1000):
        batch = building_ids[offset:offset + 1000]
        for connection in (await session.scalars(select(ServiceConnection).where(
                ServiceConnection.building_id.in_(batch)))).all():
            existing_connections[connection.building_id].append(connection)
    for building_number, (building_id, rows) in enumerate(prepared.items(), 1):
        if building_number == 1 or building_number % 500 == 0:
            logger.info('Import: reconciling building %s/%s', building_number, len(prepared))
        candidates = existing_connections[building_id]
        if replace_existing:
            building = buildings[building_id]
            if (clean_text(building.location), normalize_object_name(building.name)) in blocked:
                report['warnings'].append(dict(object=building.friendly_name or building.name,
                    location=building.location, field='connection', reason='replacement_blocked_by_incomplete_building'))
                report['skipped'] += len(rows)
                continue
            success = await replace_building_connections(session, building, rows, candidates, report)
            if success:
                numbers = list(dict.fromkeys(insurance_by_building[building_id]))
                if building.insurance_numbers != numbers:
                    building.insurance_numbers = numbers
                    if all(s == 'unchanged' for s in object_statuses[building_id]):
                        report['objects']['unchanged'] -= 1
                        report['objects']['updated'] += 1
            continue
        signatures = {build_connection_signature(values) for values, _ in rows}
        seen = set()
        for values, context in rows:
            signature = build_connection_signature(values)
            if signature in seen:
                report['warnings'].append(dict(**context, field='connection', reason='duplicate_in_batch'))
            seen.add(signature)
            try:
                connection_warnings = []
                connection, status = await upsert_service_connection(
                    session, candidates, values, signatures, warnings=connection_warnings, defer_flush=True)
                report['warnings'].extend({**w, **context} for w in connection_warnings)
            except AmbiguousMatch as exc:
                report['warnings'].append(dict(**context, field='connection', reason='ambiguous_connection', detail=str(exc)))
                report['skipped'] += 1
                continue
            report[status] += 1
            report['imported'].append(connection)
    logger.info('Import: flushing connections (created=%s updated=%s unchanged=%s skipped=%s)',
        report['created'], report['updated'], report['unchanged'], report['skipped'])
    await session.flush()


async def import_service_connections(raw_entries: list[dict], session: AsyncSession, *,
                                     replace_existing=False, dry_run=False):
    if replace_existing:
        required = ('unswitched_terminal', 'first_disconnect_point', 'source_outgoing',
                    'disconnect_point_outgoing', 'connection_notes', 'insurance_numbers')
        if not raw_entries or any(
            not clean_text(e.get('municipality')) or not clean_text(e.get('object')) or
            any(key not in e for key in required) or
            any(not isinstance(e[key], list) or any(v is None for v in e[key])
                for key in required[2:]) for e in raw_entries):
            raise ValueError('Replacement requires complete Excel rows with municipality, object and all connection fields')
    report = new_report()
    report.update(dry_run=dry_run, mode='replace_buildings' if replace_existing else 'merge',
                  deleted=0, consolidated=0, removed_connections=[])
    async with import_transaction(session, dry_run=dry_run):
        logger.info('Import: loading object index')
        objects = await load_objects(session)
        logger.info('Import: loaded %s objects', len(objects))
        await _import_connections(raw_entries, session, objects, report,
                                  replace_existing=replace_existing)
    return report


async def restore_grid_object(session, objects, asset, target_type, report):
    """Split historical building/asset collisions without changing grid or connection IDs."""
    for field in ('transformer', 'distribution_box', 'disconnect_point'):
        if field != target_type:
            references = await session.scalar(select(ServiceConnection.id).where(
                getattr(ServiceConnection, field + '_id') == asset.id).limit(1))
            if references:
                raise AmbiguousMatch(f'Existing {field} references conflict with GPS type for {asset.ckw_id}')
    candidates = [o for o in objects if o.id != asset.id and o.type == ObjectType.building
        and normalize_object_name(o.name) == normalize_object_name(asset.name)
        and clean_text(o.location) == clean_text(asset.location)]
    if len(candidates) > 1 or any(clean_text(o.ckw_id) for o in candidates):
        raise AmbiguousMatch(f'Ambiguous replacement building for {asset.name}')
    building = candidates[0] if candidates else Object(name=asset.name,
        friendly_name=asset.friendly_name, location=asset.location,
        description=asset.description, type=ObjectType.building)
    if not candidates:
        # Infrastructure coordinates are not evidence of the building's position.
        session.add(building)
        await session.flush()
        objects.append(building)
    building.insurance_numbers = list(dict.fromkeys(
        (building.insurance_numbers or []) + (asset.insurance_numbers or [])))
    connections = (await session.scalars(select(ServiceConnection).where(
        ServiceConnection.building_id == asset.id))).all()
    for connection in connections:
        connection.building_id = building.id
    asset.insurance_numbers = None
    asset.type = target_type
    report['repairs'].append(dict(asset_id=asset.id, ckw_id=asset.ckw_id,
        building_id=building.id, target_type=target_type, moved_connections=len(connections)))


async def import_gps_objects(import_data: GPSImportData, session: AsyncSession, *, return_report=False, repair_buildings=False, dry_run=False):
    report = new_report()
    report.update(dry_run=dry_run, repairs=[])
    async with import_transaction(session, dry_run=dry_run):
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
                # A CKW ID identifies infrastructure; names alone are type-scoped.
                existing = find_existing_object(objects, point.name, point.location, point.ckw_id,
                    distinct_assets=True,
                    expected_type=None if clean_text(point.ckw_id) and objects.assets[clean_text(point.ckw_id)] else obj_type)
                if not clean_text(point.name):
                    raise AmbiguousMatch('Empty object name')
                repaired = False
                if (repair_buildings and existing and existing.type == ObjectType.building
                        and point.ckw_id and obj_type in (ObjectType.transformer,
                            ObjectType.distribution_box, ObjectType.disconnect_point)):
                    await restore_grid_object(session, objects, existing, obj_type, report)
                    repaired = True
                obj, status = await upsert_object(
                    session, objects, name=point.name, location=point.location, ckw_id=point.ckw_id,
                    distinct_assets=True,
                    type=obj_type or (existing.type if existing else ObjectType.distribution_box),
                    geom=WKTElement(f'POINT({point.lon} {point.lat})', srid=4326),
                )
            except AmbiguousMatch as exc:
                report['warnings'].append(dict(object=point.name, field='object', reason='ambiguous_object', detail=str(exc)))
                report['skipped'] += 1
                continue
            report['updated' if repaired else status] += 1
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
                if entry.get('_skip_import'):
                    continue
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
