from .database import AsyncSessionLocal, engine
from .models import Object, Feeder
from .schemas import ImportData

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Object.metadata.create_all)
        await conn.run_sync(Feeder.metadata.create_all)

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