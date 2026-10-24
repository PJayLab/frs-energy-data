"""Explicit, per-building replacement of a complete Excel connection snapshot."""
from sqlalchemy import select

from app.models import ServiceConnection, ConnectionIssueReport
from app.import_helpers import CONNECTION_FIELDS, ARRAY_FIELDS, build_connection_signature


async def replace_building_connections(session, building, rows, candidates, report):
    # Rows with the same path may describe different insured structures.
    # Collect their notes rather than letting the last row erase earlier ones.
    desired = {}
    for values, context in rows:
        signature = build_connection_signature(values)
        if signature not in desired:
            desired[signature] = dict(values)
        else:
            desired[signature]['connection_notes'] = list(dict.fromkeys(
                desired[signature]['connection_notes'] + values['connection_notes']))

    ids = [c.id for c in candidates]
    protected = set((await session.scalars(select(ConnectionIssueReport.connection_id).where(
        ConnectionIssueReport.connection_id.in_(ids)))).all()) if ids else set()
    keep = {}
    for signature in desired:
        matches = [c for c in candidates if build_connection_signature(c) == signature]
        if matches:
            keep[signature] = min(matches, key=lambda c: (c.id not in protected, c.id))
    kept_ids = {c.id for c in keep.values()}
    stale = [c for c in candidates if c.id not in kept_ids]
    if any(c.id in protected for c in stale):
        report['warnings'].append(dict(object=building.friendly_name or building.name,
            location=building.location, field='connection', reason='replacement_blocked_by_issue_reports',
            connection_ids=[c.id for c in stale if c.id in protected]))
        report['skipped'] += len(rows)
        return False

    for signature, values in desired.items():
        existing = keep.get(signature)
        if existing is None:
            existing = ServiceConnection(**values)
            session.add(existing)
            await session.flush()
            status = 'created'
        else:
            status = 'unchanged'
            for field, value in values.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    status = 'updated'
        report[status] += 1
        report['imported'].append(existing)

    report['consolidated'] += len(rows) - len(desired)

    for connection in stale:
        report['removed_connections'].append(dict(
            id=connection.id, object=building.friendly_name or building.name, location=building.location,
            **{field: getattr(connection, field) for field in CONNECTION_FIELDS + ARRAY_FIELDS + ('connection_notes',)}))
        await session.delete(connection)
        report['deleted'] += 1
    return True
