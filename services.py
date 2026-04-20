from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2 import WKTElement

from frs_energy_data.database import AsyncSessionLocal, engine
from frs_energy_data.models import Object, ServiceConnection, ObjectType
from frs_energy_data.schemas import ImportData, GPSImportData


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Object.metadata.create_all)
        await conn.run_sync(ServiceConnection.metadata.create_all)


TECH_HINTS = ["PVA", "kVA", "Ladestation"]


def clean_text(value):
    if not value:
        return None
    return str(value).replace("\xa0", " ").strip()


def split_cell(value):
    if not value:
        return []
    lines = str(value).replace("\xa0", " ").split("\n")
    return [line.strip() for line in lines if line.strip()]


def split_and_clean(value):
    if not value:
        return []
    lines = str(value).replace("\xa0", " ").split("\n")
    return [line.strip() for line in lines if line.strip()]


def is_technical(texts):
    joined = " ".join(texts)
    return any(hint in joined for hint in TECH_HINTS)


def normalize_row(row):
    municipality = row[0]
    objects = split_cell(row[1])
    insurance_numbers = split_cell(row[2])
    notes = split_cell(row[3])

    unswitched_terminal = clean_text(row[4])
    first_disconnect_point = clean_text(row[5])
    disconnect_point_outgoing = split_and_clean(row[6])
    source_names = split_and_clean(row[7])
    source_outgoing = split_and_clean(row[8])

    if not objects:
        return []

    result = []
    n_obj, n_ins, n_notes = len(objects), len(insurance_numbers), len(notes)

    def make_entry(i, note_list):
        return {
            "municipality": municipality,
            "object": objects[i],
            "insurance_number": insurance_numbers[i] if i < n_ins else None,
            "connection_notes": note_list,
            "unswitched_terminal": unswitched_terminal,
            "first_disconnect_point": first_disconnect_point,
            "disconnect_point_outgoing": disconnect_point_outgoing,
            "source_name": source_names[0] if source_names else None,
            "source_outgoing": source_outgoing,
        }

    if n_obj == n_notes:
        for i in range(n_obj):
            result.append(make_entry(i, [notes[i]]))
    elif n_obj == 1:
        result.append(make_entry(0, notes))
    elif n_notes <= n_obj:
        for i in range(n_obj):
            per_obj_notes = [notes[i]] if i < n_notes else []
            result.append(make_entry(i, per_obj_notes))
    else:
        if is_technical(notes):
            for i in range(n_obj):
                result.append(make_entry(i, notes))
        else:
            for i in range(n_obj):
                per_obj_notes = [notes[i]] if i < n_notes else []
                result.append(make_entry(i, per_obj_notes))

    return result


async def import_service_connections(raw_entries: list[dict], session: AsyncSession):
    imported_connections = []
    errors = []

    for entry in raw_entries:
        object_display_name = str(entry.get("object") or entry.get("objekt") or "").strip()
        if not object_display_name:
            continue

        normalized_object_name = object_display_name.replace(" ", "")

        result = await session.execute(select(Object).where(Object.name == normalized_object_name))
        db_obj = result.scalar_one_or_none()
        if not db_obj:
            db_obj = Object(
                name=normalized_object_name,
                friendly_name=object_display_name,
                type="building",
                location=(entry.get("municipality") or entry.get("gemeinde") or "").strip() or None,
            )
            session.add(db_obj)
            await session.flush()

        async def find_object(field_name, name):
            if not name:
                return None
            normalized = str(name).replace(" ", "")
            result_inner = await session.execute(select(Object).where(Object.name == normalized))
            found = result_inner.scalar_one_or_none()
            if not found and field_name in ["building", "transformer"]:
                errors.append(
                    {
                        "object": object_display_name,
                        "field": field_name,
                        "missing_reference": name,
                    }
                )
            return found

        disconnect_point_obj = await find_object(
            "disconnect_point", entry.get("unswitched_terminal") or entry.get("tk_ohne_schalt")
        )
        distribution_box_obj = await find_object(
            "distribution_box", entry.get("first_disconnect_point") or entry.get("erste_trennstelle")
        )
        transformer_obj = await find_object("transformer", entry.get("source_name") or entry.get("speisung"))

        if not transformer_obj:
            continue

        source_outgoing = entry.get("source_outgoing") or entry.get("abgang_speisung") or []
        if isinstance(source_outgoing, str):
            source_outgoing = [source_outgoing]

        connection_notes = entry.get("connection_notes") or entry.get("bemerkungen") or []
        if isinstance(connection_notes, str):
            connection_notes = [connection_notes]

        disconnect_outgoing = entry.get("disconnect_point_outgoing") or entry.get("abgang_trennstelle") or []
        if isinstance(disconnect_outgoing, str):
            disconnect_outgoing = [disconnect_outgoing]

        service_connection = ServiceConnection(
            building_id=db_obj.id,
            transformer_id=transformer_obj.id,
            distribution_box_id=distribution_box_obj.id if distribution_box_obj else None,
            disconnect_point_id=disconnect_point_obj.id if disconnect_point_obj else None,
            disconnect_point_outgoing=disconnect_outgoing,
            source_outgoing=source_outgoing,
            connection_notes=connection_notes,
        )
        session.add(service_connection)
        imported_connections.append(service_connection)

    await session.commit()
    return {"imported": imported_connections, "errors": errors}


async def import_gps_objects(import_data: GPSImportData, session: AsyncSession):
    imported_objects = []

    for point in import_data.points:
        obj_type = point.type if point.type else "distribution_box"

        try:
            obj_type_enum = ObjectType(obj_type)
        except ValueError:
            obj_type_enum = ObjectType.distribution_box

        result = await session.execute(select(Object).where(Object.name == point.name))
        existing_obj = result.scalar_one_or_none()

        geom_point = WKTElement(f"POINT({point.lon} {point.lat})", srid=4326)

        if existing_obj:
            existing_obj.type = obj_type_enum
            existing_obj.ckw_id = point.ckw_id
            existing_obj.geom = geom_point
            session.add(existing_obj)
            imported_objects.append(existing_obj)
        else:
            db_obj = Object(
                name=point.name,
                friendly_name=None,
                type=obj_type_enum,
                ckw_id=point.ckw_id,
                geom=geom_point,
            )
            session.add(db_obj)
            imported_objects.append(db_obj)

    await session.commit()
    return imported_objects


async def import_to_db(import_data: ImportData):
    async with AsyncSessionLocal() as session:
        name_to_obj = {}

        for obj in import_data.objects:
            geom_point = None
            if obj.lon is not None and obj.lat is not None:
                geom_point = WKTElement(f"POINT({obj.lon} {obj.lat})", srid=4326)
            db_obj = Object(
                name=obj.name,
                type=obj.type.value,
                description=obj.description,
                location=obj.location,
                geom=geom_point,
            )
            session.add(db_obj)
            name_to_obj[obj.name] = db_obj

        await session.flush()

        for connection in import_data.service_connections:
            try:
                db_connection = ServiceConnection(
                    building_id=name_to_obj[connection.building_name].id,
                    transformer_id=name_to_obj[connection.transformer_name].id,
                    distribution_box_id=name_to_obj[connection.distribution_box_name].id if connection.distribution_box_name else None,
                    disconnect_point_id=name_to_obj[connection.disconnect_point_name].id if connection.disconnect_point_name else None,
                    disconnect_point_outgoing=connection.disconnect_point_outgoing,
                    source_outgoing=connection.source_outgoing,
                    connection_notes=connection.connection_notes,
                )
            except KeyError as e:
                print(f"Warning: missing object reference for service connection: {e}")
                continue
            session.add(db_connection)

        await session.commit()
