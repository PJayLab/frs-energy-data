from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from frs_energy_data.database import get_db
from frs_energy_data.models import Object

router = APIRouter(prefix="/search", tags=["Search"])


def _tokenize_query(value: str) -> list[str]:
    normalized = value.replace(",", " ").strip()
    return [token for token in normalized.split() if token]


def _address_value(obj: Object) -> str:
    return obj.friendly_name or obj.name


def _rank_score(q: str, obj: Object) -> float:
    q_low = q.lower()
    fields = [
        _address_value(obj).lower() if _address_value(obj) else "",
        (obj.location or "").lower(),
    ]
    return max((SequenceMatcher(None, q_low, field).ratio() for field in fields), default=0.0)


@router.get("/")
async def search_addresses(
    q: str,
    fields: list[str] = Query(default=["address", "location", "uuid"]),
    limit: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """
    Fuzzy-friendly address search.
    Supports spaces and comma-separated terms (e.g. "Main Street, Zurich").
    """
    tokens = _tokenize_query(q)
    if not tokens:
        return []

    filters = []
    for token in tokens:
        like_expr = f"%{token}%"
        filters.extend(
            [
                Object.name.ilike(like_expr),
                Object.friendly_name.ilike(like_expr),
                Object.location.ilike(like_expr),
            ]
        )

    stmt = (
        select(
            Object,
            Object.geom.ST_X().label("lon"),
            Object.geom.ST_Y().label("lat"),
        )
        .where(or_(*filters))
        .limit(limit * 4)
    )

    rows = (await db.execute(stmt)).all()
    ranked = sorted(rows, key=lambda row: _rank_score(q, row[0]), reverse=True)[:limit]

    allowed_fields = {"address", "location", "uuid", "lat", "lon", "type"}
    chosen = [f for f in fields if f in allowed_fields]
    if not chosen:
        chosen = ["address", "location", "uuid"]

    output = []
    for obj, lon, lat in ranked:
        candidate = {
            "address": _address_value(obj),
            "location": obj.location,
            "uuid": obj.id,
            "lat": lat,
            "lon": lon,
            "type": obj.type,
        }
        output.append({field: candidate[field] for field in chosen})

    return output


@router.get("/address/{uuid}")
async def get_address_by_uuid(uuid: str, db: AsyncSession = Depends(get_db)):
    stmt = select(
        Object,
        Object.geom.ST_X().label("lon"),
        Object.geom.ST_Y().label("lat"),
    ).where(Object.id == uuid)
    row = (await db.execute(stmt)).first()

    if not row:
        raise HTTPException(status_code=404, detail="Address not found")

    obj, lon, lat = row
    return {
        "uuid": obj.id,
        "address": _address_value(obj),
        "location": obj.location,
        "type": obj.type,
        "description": obj.description,
        "lat": lat,
        "lon": lon,
    }


@router.get("/nearby")
async def nearby_addresses(
    lat: float,
    lon: float,
    radius: int = Query(default=500, ge=1, le=50000),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    point_wkt = f"SRID=4326;POINT({lon} {lat})"

    distance_expr = func.ST_Distance(
        func.ST_Transform(Object.geom, 3857),
        func.ST_Transform(func.ST_GeomFromEWKT(point_wkt), 3857),
    )
    stmt = (
        select(
            Object,
            Object.geom.ST_X().label("lon"),
            Object.geom.ST_Y().label("lat"),
            distance_expr.label("distance_m"),
        )
        .where(
            func.ST_DWithin(
                func.ST_Transform(Object.geom, 3857),
                func.ST_Transform(func.ST_GeomFromEWKT(point_wkt), 3857),
                radius,
            )
        )
        .order_by(distance_expr.asc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    return [
        {
            "uuid": obj.id,
            "address": _address_value(obj),
            "location": obj.location,
            "type": obj.type,
            "lat": row_lat,
            "lon": row_lon,
            "distance_m": float(distance_m) if distance_m is not None else None,
        }
        for obj, row_lon, row_lat, distance_m in rows
    ]
