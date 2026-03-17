from fastapi import FastAPI, Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db, engine, Base
from crud import search_objekte
from schemas import ObjektOut
import asyncio

app = FastAPI()

# Tabellen erstellen (nur für Demo)
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# REST API Suche
@app.get("/suche", response_model=list[ObjektOut])
async def suche(q: str, db: AsyncSession = Depends(get_db)):
    result = await search_objekte(db, q)
    return result

# WebSocket (z.B. für Live-Daten / Netzstatus)
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    
    while True:
        data = {
            "status": "ok",
            "message": "Live Netzdaten",
            "last_update": str(asyncio.get_event_loop().time())
        }
        await ws.send_json(data)
        await asyncio.sleep(5)