import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db, engine, Base
from models import Object, Feeder
from api.import_router import router as import_router


# ----------------------
# Lifespan
# ----------------------
@asynccontextmanager
async def lifespan(app: FastAPI):

    # DB + PostGIS Tabellen erstellen
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app.state.ws_clients = set()

    yield

    # Shutdown
    for ws in app.state.ws_clients:
        await ws.close()


# ----------------------
# App
# ----------------------
app = FastAPI(lifespan=lifespan)
app.include_router(import_router)


# ----------------------
# SEARCH API (NEU)
# ----------------------
@app.get("/search")
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
@app.get("/feeder/{building_name}")
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


# ----------------------
# WebSocket
# ----------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws.app.state.ws_clients.add(ws)

    try:
        while True:
            data = {
                "status": "ok",
                "message": "Live grid data",
                "timestamp": asyncio.get_event_loop().time()
            }
            await ws.send_json(data)
            await asyncio.sleep(5)

    except Exception:
        pass

    finally:
        ws.app.state.ws_clients.discard(ws)
        await ws.close()