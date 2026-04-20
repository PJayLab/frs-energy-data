from sqlalchemy import select
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from models import Object, ServiceConnection


async def create_object(db, obj):
    geom = from_shape(Point(obj.lon, obj.lat), srid=4326)

    db_obj = Object(
        name=obj.name,
        type=obj.type,
        description=obj.description,
        geom=geom,
    )

    db.add(db_obj)
    await db.flush()
    return db_obj


async def get_object_by_name(db, name: str):
    result = await db.execute(select(Object).where(Object.name == name))
    return result.scalar_one_or_none()


async def create_service_connection(db, connection):
    building = await get_object_by_name(db, connection.building_name)
    transformer = await get_object_by_name(db, connection.transformer_name)

    distribution_box = None
    disconnect_point = None

    if connection.distribution_box_name:
        distribution_box = await get_object_by_name(db, connection.distribution_box_name)

    if connection.disconnect_point_name:
        disconnect_point = await get_object_by_name(db, connection.disconnect_point_name)

    db_connection = ServiceConnection(
        building_id=building.id,
        transformer_id=transformer.id,
        distribution_box_id=distribution_box.id if distribution_box else None,
        disconnect_point_id=disconnect_point.id if disconnect_point else None,
        disconnect_point_outgoing=connection.disconnect_point_outgoing,
        source_outgoing=connection.source_outgoing,
        connection_notes=connection.connection_notes,
        fuse_rating=connection.fuse_rating,
    )

    db.add(db_connection)
