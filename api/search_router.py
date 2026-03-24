from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from frs_energy_data.models import Object, Feeder
from frs_energy_data.database import get_db

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("/search2")
async def search(
    q: str,
    type: str = Query(default="building"),
    db: AsyncSession = Depends(get_db)
):

    # Aliases
    Building = aliased(Object)
    Transformer = aliased(Object)
    DistBox = aliased(Object)
    DiscPoint = aliased(Object)

    # ----------------------
    # BUILDING SEARCH
    # ----------------------
    if type == "building":

        stmt = (
            select(
                Feeder,

                # ganze Objekte
                Building,
                Transformer,
                DistBox,
                DiscPoint,

                # Geokoordinaten direkt mitladen
                Building.geom.ST_X().label("b_lon"),
                Building.geom.ST_Y().label("b_lat"),

                Transformer.geom.ST_X().label("t_lon"),
                Transformer.geom.ST_Y().label("t_lat"),

                DistBox.geom.ST_X().label("d_lon"),
                DistBox.geom.ST_Y().label("d_lat"),

                DiscPoint.geom.ST_X().label("dp_lon"),
                DiscPoint.geom.ST_Y().label("dp_lat"),
            )
            .join(Building, Feeder.building_id == Building.id)
            .join(Transformer, Feeder.transformer_id == Transformer.id)
            .outerjoin(DistBox, Feeder.distribution_box_id == DistBox.id)
            .outerjoin(DiscPoint, Feeder.disconnect_point_id == DiscPoint.id)
            .where(Building.name.ilike(f"%{q}%"))
        )

        result = await db.execute(stmt)
        rows = result.all()

        output = []

        for row in rows:
            (
                feeder,
                b,
                t,
                d,
                dp,
                b_lon, b_lat,
                t_lon, t_lat,
                d_lon, d_lat,
                dp_lon, dp_lat
            ) = row

            def geo(obj, lon, lat):
                if not obj:
                    return None
                return {
                    "id": obj.id,
                    "name": obj.name,
                    "lat": lat,
                    "lon": lon
                }

            output.append({
                "building": geo(b, b_lon, b_lat),

                "feeder": {
                    "label": feeder.feeder_label,
                    "fuse_rating": feeder.fuse_rating,
                    "notes": feeder.notes
                },

                "transformer": geo(t, t_lon, t_lat),
                "distribution_box": geo(d, d_lon, d_lat),
                "disconnect_point": geo(dp, dp_lon, dp_lat)
            })

        return output

    # ----------------------
    # FALLBACK SEARCH
    # ----------------------
    else:
        stmt = (
            select(
                Object,
                Object.geom.ST_X().label("lon"),
                Object.geom.ST_Y().label("lat")
            )
            .where(Object.name.ilike(f"%{q}%"))
        )

        result = await db.execute(stmt)
        rows = result.all()

        return [
            {
                "id": obj.id,
                "name": obj.name,
                "type": obj.type,
                "lat": lat,
                "lon": lon
            }
            for obj, lon, lat in rows
        ]
    
    # ----------------------
# SEARCH API (NEU)
# ----------------------
@router.get("/search")
async def search(q: str, db: AsyncSession = Depends(get_db)):

    result = await db.execute(
        select(Object).where(Object.name.ilike(f"%{q}%"))
    )
    objects = result.scalars().all()

    output = []

    for obj in objects:
        # Geo auslesen (PostGIS)
        geom_result = await db.execute(
            select(
                Object.id,
                Object.name,
                Object.type,
                Object.description,
                Object.geom.ST_X().label("lon"),
                Object.geom.ST_Y().label("lat")
            ).where(Object.id == obj.id)
        )

        geo = geom_result.first()

        output.append({
            "id": geo.id,
            "name": geo.name,
            "type": geo.type,
            "description": geo.description,
            "lat": geo.lat,
            "lon": geo.lon
        })

    return output


# ----------------------
# FEEDER DETAIL API
# ----------------------
@router.get("/feeder/{building_name}")
async def get_feeder(building_name: str, db: AsyncSession = Depends(get_db)):

    result = await db.execute(
        select(Feeder)
        .join(Object, Feeder.building_id == Object.id)
        .where(Object.name.ilike(building_name))
    )

    feeder = result.scalar_one_or_none()

    if not feeder:
        return {"error": "not found"}

    # zugehörige Objekte holen
    async def get_obj(obj_id):
        if not obj_id:
            return None
        res = await db.execute(select(Object).where(Object.id == obj_id))
        return res.scalar_one()

    building = await get_obj(feeder.building_id)
    transformer = await get_obj(feeder.transformer_id)
    dist = await get_obj(feeder.distribution_box_id)
    disc = await get_obj(feeder.disconnect_point_id)

    return {
        "building": building.name,
        "transformer": transformer.name,
        "distribution_box": dist.name if dist else None,
        "disconnect_point": disc.name if disc else None,
        "feeder_label": feeder.feeder_label,
        "fuse_rating": feeder.fuse_rating,
        "notes": feeder.notes
    }