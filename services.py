from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2 import WKTElement
import json

from frs_energy_data.database import AsyncSessionLocal, engine
from frs_energy_data.models import Object, Feeder, ObjectType
from frs_energy_data.schemas import ImportData, GPSImportData
from frs_energy_data.utils import normalize_obj_name

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Object.metadata.create_all)
        await conn.run_sync(Feeder.metadata.create_all)

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
    """
    Trennt Zeilen/Abstände, entfernt leere Einträge.
    z.B. "19 \n20" -> ["19", "20"]
    """
    if not value:
        return []
    lines = str(value).replace("\xa0", " ").split("\n")
    return [line.strip() for line in lines if line.strip()]

def is_technical(texts):
    joined = " ".join(texts)
    return any(hint in joined for hint in TECH_HINTS)

def normalize_row(row):
    gemeinde = row[0]
    objekte = split_cell(row[1])
    assek = split_cell(row[2])
    bemerkungen = split_cell(row[3])

    tk_ohne_schalt = clean_text(row[4])
    erste_trennstelle = clean_text(row[5])
    abgang_trennstelle = clean_text(row[6])
    speisung_raw = row[7]
    abgang_speisung_raw = row[8]

    speisung = split_and_clean(speisung_raw)
    abgang_speisung = split_and_clean(abgang_speisung_raw)

    if not objekte:
        return []

    result = []
    n_obj, n_ass, n_bem = len(objekte), len(assek), len(bemerkungen)

    def make_entry(i, bem_list, feeder_label=None):
        return {
            "gemeinde": gemeinde,
            "objekt": objekte[i],
            "assek_nr": assek[i] if i < n_ass else None,
            "bemerkungen": bem_list,
            "tk_ohne_schalt": tk_ohne_schalt,
            "erste_trennstelle": erste_trennstelle,
            "abgang_trennstelle": abgang_trennstelle,
            "speisung": speisung[0] if speisung else None,  # nur 1x speichern
            "abgang_speisung": feeder_label,  # kann Array sein
        }

    # Fall A: gleiche Anzahl Bemerkungen wie Objekte
    if n_obj == n_bem:
        for i in range(n_obj):
            result.append(make_entry(i, [bemerkungen[i]], abgang_speisung))
    # Fall B: nur 1 Objekt
    elif n_obj == 1:
        result.append(make_entry(0, bemerkungen, abgang_speisung))
    # Fall C: weniger Bemerkungen als Objekte
    elif n_bem <= n_obj:
        for i in range(n_obj):
            bem = [bemerkungen[i]] if i < n_bem else []
            result.append(make_entry(i, bem, abgang_speisung))
    # Fall D: mehr Bemerkungen als Objekte
    else:
        if is_technical(bemerkungen):
            for i in range(n_obj):
                result.append(make_entry(i, bemerkungen, abgang_speisung))
        else:
            for i in range(n_obj):
                bem = [bemerkungen[i]] if i < n_bem else []
                result.append(make_entry(i, bem, abgang_speisung))

    return result

async def import_feeders_objects(raw_entries: list[dict], session: AsyncSession):
    """
    Importiert Objekte + Feeders in die 'objects'-Tabelle.
    Speichert fehlende Pflichtobjekte in errors.
    """
    imported_feeders = []
    errors = []

    for entry in raw_entries:
        obj_name = entry["objekt"].strip()
        normalized_obj_name = obj_name.replace(" ", "")  # Leerzeichen entfernen

        # --- Objekt selbst erstellen ---
        result = await session.execute(select(Object).where(Object.name == normalized_obj_name))
        db_obj = result.scalar_one_or_none()
        if not db_obj:
            db_obj = Object(name=normalized_obj_name, type="building")
            session.add(db_obj)
            await session.flush()  # ID sofort verfügbar

        # --- Referenzen suchen ---
        async def find_object(field_name, name):
            if not name:
                return None
            name_norm = name.replace(" ", "")
            result = await session.execute(select(Object).where(Object.name == name_norm))
            obj_found = result.scalar_one_or_none()
            if not obj_found and field_name in ["building", "transformer"]:
                errors.append({
                    "object": obj_name,
                    "feld": field_name,
                    "fehlende_referenz": name
                })
            return obj_found

        tk_obj = await find_object("disconnect_point", entry.get("tk_ohne_schalt"))
        db_obj_ref = await find_object("distribution_box", entry.get("erste_trennstelle"))
        tr_obj = await find_object("transformer", entry.get("speisung"))

        # Pflichtfeld transformer fehlt → Feeder überspringen
        if not tr_obj:
            continue

        # --- Feeder erstellen ---
        feeder = Feeder(
            building_id=db_obj.id,
            transformer_id=tr_obj.id,
            distribution_box_id=db_obj_ref.id if db_obj_ref else None,
            disconnect_point_id=tk_obj.id if tk_obj else None,
            feeder_label=json.dumps(entry.get("abgang_speisung", [])),  # Array als JSON speichern
            notes=json.dumps(entry.get("bemerkungen", []))
        )
        session.add(feeder)
        imported_feeders.append(feeder)

    await session.commit()
    return {"imported": imported_feeders, "errors": errors}

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