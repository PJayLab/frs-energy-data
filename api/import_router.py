from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from frs_energy_data.schemas import ImportData, GPSImportData
from frs_energy_data.services import import_to_db, import_gps_objects, import_feeders_objects
from frs_energy_data.database import get_db

router = APIRouter(prefix="/import", tags=["Import"])

@router.post("/feeders")
async def import_feeders(payload: list[dict], db: AsyncSession = Depends(get_db)):
    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    feeders = await import_feeders_objects(payload, db)
    return {"status": "success", "imported": len(feeders)}

@router.post("/gps")
async def import_gps(payload: GPSImportData, db: AsyncSession = Depends(get_db)):
    """
    Importiert nur GPS-Points (Objects) in die Datenbank.
    """
    if not payload.points:
        raise HTTPException(status_code=400, detail="Empty payload")

    imported_objects = await import_gps_objects(payload, db)
    return {"status": "success", "imported": len(imported_objects)}

@router.post("/gps_old")
async def import_gps(payload: list[dict], db: AsyncSession = Depends(get_db)):
    """
    Importiere JSON Payload direkt in die DB.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    import_data = ImportData(raw_entries=payload)
    await import_to_db(import_data)
    return {"status": "success", "objects": len(import_data.objects), "feeders": len(import_data.feeders)}