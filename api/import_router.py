from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from ..schemas import ImportData
from ..services import import_to_db
from ..database import get_db

router = APIRouter(prefix="/import", tags=["Import"])

@router.post("/json")
async def import_json(payload: list[dict], db: AsyncSession = Depends(get_db)):
    """
    Importiere JSON Payload direkt in die DB.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    import_data = ImportData(raw_entries=payload)
    await import_to_db(import_data)
    return {"status": "success", "objects": len(import_data.objects), "feeders": len(import_data.feeders)}