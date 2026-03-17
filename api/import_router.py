from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import ImportData
from crud import create_object, create_feeder

router = APIRouter()


@router.post("/import-test-data")
async def import_test_data(data: ImportData, db: AsyncSession = Depends(get_db)):

    # Objekte zuerst erstellen
    for obj in data.objects:
        await create_object(db, obj)

    # dann feeders
    for feeder in data.feeders:
        await create_feeder(db, feeder)

    await db.commit()

    return {"status": "ok"}