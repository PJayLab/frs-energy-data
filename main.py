import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from frs_energy_data.database import get_db, engine, Base
from frs_energy_data.models import Object, ServiceConnection
from frs_energy_data.api.import_router import router as import_router
from frs_energy_data.api.search_router import router as search_router


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
app.include_router(search_router)

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