from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2 import WKTElement

from frs_energy_data.database import AsyncSessionLocal, engine
from frs_energy_data.models import Object, Feeder, ObjectType
from frs_energy_data.schemas import ImportData, GPSImportData
from frs_energy_data.utils import normalize_obj_name

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Object.metadata.create_all)
        await conn.run_sync(Feeder.metadata.create_all)

async def import_feeders_objects(raw_entries: list[dict], session: AsyncSession):
    """
    Importiert Objekte + Feeders. Alles in einer Tabelle 'objects'.
    """
    imported_feeders = []

    for entry in raw_entries:
        # --- Objekt selbst ---
        obj_name = entry["objekt"].strip()
        normalized_obj_name = normalize_obj_name(obj_name)

        # Prüfen, ob Objekt existiert
        result = await session.execute(select(Object).where(Object.name == normalized_obj_name))
        db_obj = result.scalar_one_or_none()
        if not db_obj:
            db_obj = Object(name=normalized_obj_name, type="building")  # Gebäude
            session.add(db_obj)
            await session.flush()  # ID sofort verfügbar

        # --- Referenzen suchen ---
        async  def find_object_by_name(name: str):
            if not name:
                return None
            name_norm = normalize_obj_name(name)
            result = await session.execute(select(Object).where(Object.name == name_norm))
            obj_found = result.scalar_one_or_none()
            return obj_found

        tk_name = entry.get("tk_ohne_schalt")
        tk_obj = await find_object_by_name(tk_name) if tk_name else None

        db_name = entry.get("erste_trennstelle")
        db_obj_ref = await find_object_by_name(db_name) if db_name else None

        tr_name = entry.get("speisung")
        tr_obj = await find_object_by_name(tr_name) if tr_name else None

        # --- Feeder anlegen ---
        feeder = Feeder(
            building_id=db_obj.id,
            transformer_id=tr_obj.id if tr_obj else None,
            distribution_box_id=db_obj_ref.id if db_obj_ref else None,
            disconnect_point_id=tk_obj.id if tk_obj else None,
            feeder_label=f"{entry.get('abgang_speisung')}" if entry.get("abgang_speisung") else None,
            notes="; ".join(entry.get("bemerkungen", [])) if entry.get("bemerkungen") else None
        )
        session.add(feeder)
        imported_feeders.append(feeder)

    await session.commit()
    return imported_feeders

async def import_gps_objects(import_data: GPSImportData, session: AsyncSession):
    """
    Importiert nur GPS-Objects, speichert lat/lon in PostGIS geom-Spalte.
    Aktualisiert vorhandene Objekte oder fügt neue ein.
    """
    imported_objects = []

    for point in import_data.points:
        obj_type = point.type if point.type else "distribution_box"

        # Enum Konvertierung
        try:
            obj_type_enum = ObjectType(obj_type)
        except ValueError:
            obj_type_enum = ObjectType.distribution_box

        # Prüfen, ob Objekt bereits existiert
        result = await session.execute(select(Object).where(Object.name == point.name))
        existing_obj = result.scalar_one_or_none()

        # PostGIS POINT erzeugen
        geom_point = WKTElement(f"POINT({point.lon} {point.lat})", srid=4326)

        if existing_obj:
            # Update
            existing_obj.type = obj_type_enum
            existing_obj.ckw_id = point.ckw_id
            existing_obj.geom = geom_point
            session.add(existing_obj)
            imported_objects.append(existing_obj)
        else:
            # Neues Objekt
            db_obj = Object(
                name=point.name,
                type=obj_type_enum,
                ckw_id=point.ckw_id,
                geom=geom_point
            )
            session.add(db_obj)
            imported_objects.append(db_obj)

    await session.commit()
    return imported_objects

async def import_to_db(import_data: ImportData):
    async with AsyncSessionLocal() as session:
        name_to_obj = {}

        for obj in import_data.objects:
            db_obj = Object(name=obj.name, type=obj.type.value, description=obj.description, lat=obj.lat, lon=obj.lon)
            session.add(db_obj)
            name_to_obj[obj.name] = db_obj

        await session.commit()

        for feeder in import_data.feeders:
            try:
                db_feeder = Feeder(
                    building_id=name_to_obj[feeder.building_name].id,
                    transformer_id=name_to_obj[feeder.transformer_name].id,
                    distribution_box_id=name_to_obj[feeder.distribution_box_name].id if feeder.distribution_box_name else None,
                    disconnect_point_id=name_to_obj[feeder.disconnect_point_name].id if feeder.disconnect_point_name else None,
                    feeder_label=feeder.feeder_label,
                    fuse_rating=feeder.fuse_rating,
                    notes=feeder.notes,
                )
            except KeyError as e:
                print(f"Warnung: Objekt nicht gefunden für Feeder: {e}")
                continue
            session.add(db_feeder)

        await session.commit()