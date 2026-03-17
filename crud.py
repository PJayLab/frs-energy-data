from sqlalchemy import select
from models import Objekt

async def search_objekte(db, query: str):
    result = await db.execute(
        select(Objekt).where(Objekt.name.ilike(f"%{query}%"))
    )
    return result.scalars().all()