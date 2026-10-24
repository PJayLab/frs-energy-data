"""Shared import identities and conservative update rules."""
from contextlib import asynccontextmanager
from collections import defaultdict
import re
import unicodedata
from geoalchemy2.shape import to_shape
from sqlalchemy import select, text
from app.models import Object, ServiceConnection


def clean_text(value):
    if value is None:
        return None
    return str(value).replace('\xa0', ' ').strip() or None


def normalize_object_name(value):
    return ''.join((clean_text(value) or '').split())


def reference_name_key(value):
    """Conservative reference-only aliases; never remove house numbers/punctuation."""
    value = unicodedata.normalize('NFC', clean_text(value) or '').casefold()
    value = re.sub(r'str\.(?=\s|\w|$)', 'strasse', value)
    return ''.join(value.split())


def unique_reference_names(value):
    names = {}
    for name in split_compact(value):
        names.setdefault(normalize_object_name(name), name)
    return list(names.values())


def split_positional(value):
    if value is None:
        return []
    values = [line.strip() for line in str(value).replace('\xa0', ' ').splitlines()]
    while values and not values[-1]:
        values.pop()
    return values


def split_compact(value):
    if isinstance(value, (list, tuple)):
        return [part for item in value for part in split_compact(item)]
    return [part for part in split_positional(value) if part]


def normalize_array_for_comparison(value):
    # Preserve order: outgoing arrays may describe corresponding paths.
    return tuple(split_compact(value))


class AmbiguousMatch(ValueError):
    pass


class ObjectIndex(list):
    """Batch-local indices also include freshly inserted and renamed objects."""
    def __init__(self, objects):
        super().__init__()
        self.names = defaultdict(list)
        self.assets = defaultdict(list)
        self.reference_names = defaultdict(list)
        for obj in objects:
            self.append(obj)

    def append(self, obj):
        super().append(obj)
        self.names[normalize_object_name(obj.name)].append(obj)
        self.assets[clean_text(obj.ckw_id)].append(obj)
        self.reference_names[reference_name_key(obj.name)].append(obj)

    def reindex(self, obj, previous_name, previous_asset):
        self.names[normalize_object_name(previous_name)].remove(obj)
        self.assets[previous_asset].remove(obj)
        self.reference_names[reference_name_key(previous_name)].remove(obj)
        self.names[normalize_object_name(obj.name)].append(obj)
        self.assets[clean_text(obj.ckw_id)].append(obj)
        self.reference_names[reference_name_key(obj.name)].append(obj)


@asynccontextmanager
async def import_transaction(session):
    """Own one commit/rollback, including an existing authentication read.

    Serialize imports on PostgreSQL to prevent concurrent check/insert races.
    Callers must not pass sessions containing unrelated pending writes.
    """
    try:
        if session.get_bind().dialect.name == 'postgresql':
            await session.execute(text('SELECT pg_advisory_xact_lock(724183901)'))
        yield
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


def _unique(candidates, identity):
    if len(candidates) > 1:
        raise AmbiguousMatch(f'Multiple objects match {identity}')
    return candidates[0] if candidates else None


def find_existing_object(objects, name, location=None, ckw_id=None):
    location, ckw_id = clean_text(location), clean_text(ckw_id)
    if ckw_id:
        assets = objects.assets[ckw_id] if isinstance(objects, ObjectIndex) else [o for o in objects if clean_text(o.ckw_id) == ckw_id]
        found = _unique(assets, f'ckw_id={ckw_id}')
        if found:
            return found
    candidates = objects.names[normalize_object_name(name)] if isinstance(objects, ObjectIndex) else [o for o in objects if normalize_object_name(o.name) == normalize_object_name(name)]
    if location:
        exact = [o for o in candidates if clean_text(o.location) == location]
        candidates = exact or [o for o in candidates if not clean_text(o.location)]
    found = _unique(candidates, f'name={name}, location={location}')
    if found and ckw_id and clean_text(found.ckw_id) not in (None, ckw_id):
        raise AmbiguousMatch(f'Conflicting asset IDs for {name}')
    return found


def find_reference_object(objects, name, location=None):
    # Preserve exact matching and its ambiguity checks before trying aliases.
    found = find_existing_object(objects, name, location)
    if found:
        return found, False
    key = reference_name_key(name)
    candidates = (objects.reference_names[key] if isinstance(objects, ObjectIndex)
                  else [o for o in objects if reference_name_key(o.name) == key])
    if location:
        local = [o for o in candidates if reference_name_key(o.location) == reference_name_key(location)]
        unknown = [o for o in candidates if not clean_text(o.location)]
        if local or unknown:
            return _unique(local or unknown, f'reference={name}, location={location}'), True
    # An exact global name is preferred over a global alias (cross-border assets).
    found = find_existing_object(objects, name)
    if found:
        return found, False
    return _unique(candidates, f'reference={name}'), True


def _value_key(value):
    if hasattr(value, 'srid'):
        return (value.srid, to_shape(value).wkb_hex)
    return value


async def upsert_object(session, objects, *, name, location=None, ckw_id=None, **attributes):
    display = clean_text(name)
    if not display:
        raise AmbiguousMatch('Empty object name')
    existing = find_existing_object(objects, display, location, ckw_id)
    values = dict(name=normalize_object_name(display), location=clean_text(location), ckw_id=clean_text(ckw_id), **attributes)
    # Compact GPS names must not destroy a previously formatted display name.
    if not existing or not existing.friendly_name or normalize_object_name(existing.friendly_name) != values['name'] or display != values['name']:
        values['friendly_name'] = display
    if existing is None:
        existing = Object(**{key: value for key, value in values.items() if value is not None})
        session.add(existing)
        await session.flush()
        objects.append(existing)
        return existing, 'created'
    changed = False
    previous_name, previous_asset = existing.name, clean_text(existing.ckw_id)
    for key, value in values.items():
        if value is not None and _value_key(getattr(existing, key)) != _value_key(value):
            setattr(existing, key, value)
            changed = True
    if changed and isinstance(objects, ObjectIndex):
        objects.reindex(existing, previous_name, previous_asset)
    return existing, 'updated' if changed else 'unchanged'


CONNECTION_FIELDS = ('building_id', 'transformer_id', 'distribution_box_id', 'disconnect_point_id')
ARRAY_FIELDS = ('source_outgoing', 'disconnect_point_outgoing')


def build_connection_signature(values):
    get = values.get if isinstance(values, dict) else lambda key: getattr(values, key)
    return tuple(get(key) for key in CONNECTION_FIELDS) + tuple(normalize_array_for_comparison(get(key)) for key in ARRAY_FIELDS)


def find_existing_service_connection(candidates, values, incoming_signatures):
    # Omitted fields are unknown and preserve stored values.
    matches = [c for c in candidates if all(
        (normalize_array_for_comparison(getattr(c, key)) == normalize_array_for_comparison(value)
         if key in ARRAY_FIELDS else getattr(c, key) == value)
        for key, value in values.items() if key in CONNECTION_FIELDS + ARRAY_FIELDS
    )]
    if len(matches) > 1:
        if len({connection_content_key(c) for c in matches}) != 1:
            raise AmbiguousMatch('Multiple existing connections differ in stored fields or notes')
        return min(matches, key=lambda c: c.id)
    if matches:
        return matches[0]
    if len(candidates) == 1 and len(incoming_signatures) == 1:
        return candidates[0]
    if candidates and not all(build_connection_signature(c) in incoming_signatures for c in candidates):
        raise AmbiguousMatch('Connection edit cannot be assigned to one existing connection')
    return None


def connection_content_key(connection):
    return build_connection_signature(connection), normalize_array_for_comparison(connection.connection_notes)


async def upsert_service_connection(session, candidates, values, incoming_signatures, *, warnings=None):
    existing = find_existing_service_connection(candidates, values, incoming_signatures)
    if existing is None:
        existing = ServiceConnection(**values)
        session.add(existing)
        await session.flush()
        candidates.append(existing)
        return existing, 'created'
    # Preserve every ID (including issue-report references). Only equivalent
    # records can be synchronized; differences still require explicit review.
    copies = [c for c in candidates if connection_content_key(c) == connection_content_key(existing)]
    if len(copies) > 1 and warnings is not None:
        warnings.append(dict(field='connection', reason='duplicate_connections_synchronized',
                             connection_ids=sorted(c.id for c in copies)))
    changed = False
    for copy in copies:
        for key, value in values.items():
            if getattr(copy, key) != value:
                setattr(copy, key, value)
                changed = True
    return existing, 'updated' if changed else 'unchanged'


def new_report():
    return dict(created=0, updated=0, unchanged=0, skipped=0, errors=[], warnings=[], imported=[],
                objects=dict(created=0, updated=0, unchanged=0))


async def load_objects(session):
    # Normalize historical GPS names too; a batch cache also sees pending inserts.
    return ObjectIndex((await session.scalars(select(Object))).all())
