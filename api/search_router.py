from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from frs_energy_data.database import get_db
from frs_energy_data.models import Object, ServiceConnection, ConnectionIssueReport
from frs_energy_data.utils import normalize_name
from frs_energy_data.schemas import ConnectionIssueReportCreate

router = APIRouter(prefix="/search", tags=["Search"])


def _tokenize_query(value: str) -> list[str]:
    normalized = value.replace(",", " ").strip()
    return [token for token in normalized.split() if token]


def _compact(value: str) -> str:
    return normalize_name(value or "")


def _address_value(obj: Object) -> str:
    return obj.friendly_name or obj.name


def _rank_score(q: str, obj: Object) -> float:
    q_raw = q.lower()
    q_compact = _compact(q)

    fields_raw = [_address_value(obj) or "", obj.location or ""]
    fields_compact = [_compact(item) for item in fields_raw]

    raw_score = max((SequenceMatcher(None, q_raw, item.lower()).ratio() for item in fields_raw), default=0.0)
    compact_score = max((SequenceMatcher(None, q_compact, item).ratio() for item in fields_compact), default=0.0)
    return max(raw_score, compact_score)


def _address_filters(q: str):
    tokens = _tokenize_query(q)
    compact_q = _compact(q)

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

    if compact_q:
        compact_like = f"%{compact_q}%"
        filters.extend(
            [
                func.lower(func.regexp_replace(func.coalesce(Object.name, ""), r"[^[:alnum:]]", "", "g")).ilike(compact_like),
                func.lower(func.regexp_replace(func.coalesce(Object.friendly_name, ""), r"[^[:alnum:]]", "", "g")).ilike(compact_like),
            ]
        )

    return filters


async def _search_addresses_internal(q: str, fields: list[str], limit: int, db: AsyncSession):
    filters = _address_filters(q)
    if not filters:
        return []

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
    chosen = [f for f in fields if f in allowed_fields] or ["address", "location", "uuid"]

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


@router.get("/")
async def search_addresses(
    q: str,
    fields: list[str] = Query(default=["address", "location", "uuid"]),
    limit: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await _search_addresses_internal(q=q, fields=fields, limit=limit, db=db)


@router.get("/address")
async def search_addresses_alias(
    q: str,
    fields: list[str] = Query(default=["address", "location", "uuid"]),
    limit: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await _search_addresses_internal(q=q, fields=fields, limit=limit, db=db)


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


@router.get("/connection")
async def search_connections(
    q: str,
    fields: list[str] = Query(default=["address", "location", "connection_uuid"]),
    limit: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    Building = aliased(Object)

    tokens = _tokenize_query(q)
    compact_q = _compact(q)
    filters = []

    for token in tokens:
        like_expr = f"%{token}%"
        filters.extend(
            [
                Building.name.ilike(like_expr),
                Building.friendly_name.ilike(like_expr),
                Building.location.ilike(like_expr),
            ]
        )

    if compact_q:
        compact_like = f"%{compact_q}%"
        filters.extend(
            [
                func.lower(func.regexp_replace(func.coalesce(Building.name, ""), r"[^[:alnum:]]", "", "g")).ilike(compact_like),
                func.lower(func.regexp_replace(func.coalesce(Building.friendly_name, ""), r"[^[:alnum:]]", "", "g")).ilike(compact_like),
            ]
        )

    if not filters:
        return []

    stmt = (
        select(
            ServiceConnection,
            Building,
            Building.geom.ST_X().label("b_lon"),
            Building.geom.ST_Y().label("b_lat"),
        )
        .join(Building, ServiceConnection.building_id == Building.id)
        .where(or_(*filters))
        .limit(limit * 4)
    )
    rows = (await db.execute(stmt)).all()
    ranked = sorted(rows, key=lambda row: _rank_score(q, row[1]), reverse=True)[:limit]

    allowed_fields = {"address", "location", "uuid", "lat", "lon", "type", "connection_uuid"}
    chosen = [f for f in fields if f in allowed_fields] or ["address", "location", "connection_uuid"]

    output = []
    for connection, building, b_lon, b_lat in ranked:
        candidate = {
            "address": _address_value(building),
            "location": building.location,
            "uuid": building.id,
            "connection_uuid": connection.id,
            "lat": b_lat,
            "lon": b_lon,
            "type": building.type,
        }
        output.append({field: candidate[field] for field in chosen})

    return output


@router.get("/connection/{uuid}")
async def get_connection_by_uuid(uuid: str, db: AsyncSession = Depends(get_db)):
    Building = aliased(Object)
    Transformer = aliased(Object)
    DistributionBox = aliased(Object)
    DisconnectPoint = aliased(Object)

    stmt = (
        select(
            ServiceConnection,
            Building,
            Transformer,
            DistributionBox,
            DisconnectPoint,
            Building.geom.ST_X().label("b_lon"),
            Building.geom.ST_Y().label("b_lat"),
            Transformer.geom.ST_X().label("t_lon"),
            Transformer.geom.ST_Y().label("t_lat"),
            DistributionBox.geom.ST_X().label("d_lon"),
            DistributionBox.geom.ST_Y().label("d_lat"),
            DisconnectPoint.geom.ST_X().label("dp_lon"),
            DisconnectPoint.geom.ST_Y().label("dp_lat"),
        )
        .join(Building, ServiceConnection.building_id == Building.id)
        .join(Transformer, ServiceConnection.transformer_id == Transformer.id)
        .outerjoin(DistributionBox, ServiceConnection.distribution_box_id == DistributionBox.id)
        .outerjoin(DisconnectPoint, ServiceConnection.disconnect_point_id == DisconnectPoint.id)
        .where(ServiceConnection.id == uuid)
    )

    row = (await db.execute(stmt)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found")

    (
        connection,
        building,
        transformer,
        distribution_box,
        disconnect_point,
        b_lon,
        b_lat,
        t_lon,
        t_lat,
        d_lon,
        d_lat,
        dp_lon,
        dp_lat,
    ) = row

    def obj_payload(obj, lon, lat):
        if not obj:
            return None
        return {
            "uuid": obj.id,
            "address": _address_value(obj),
            "location": obj.location,
            "type": obj.type,
            "lat": lat,
            "lon": lon,
        }

    return {
        "connection": {
            "uuid": connection.id,
            "disconnect_point_outgoing": connection.disconnect_point_outgoing or [],
            "source_outgoing": connection.source_outgoing or [],
            "connection_notes": connection.connection_notes or [],
        },
        "building": obj_payload(building, b_lon, b_lat),
        "transformer": obj_payload(transformer, t_lon, t_lat),
        "distribution_box": obj_payload(distribution_box, d_lon, d_lat),
        "disconnect_point": obj_payload(disconnect_point, dp_lon, dp_lat),
    }


@router.post("/connection/{uuid}/report")
async def report_wrong_connection(
    uuid: str,
    payload: ConnectionIssueReportCreate,
    db: AsyncSession = Depends(get_db),
):
    connection = await db.get(ServiceConnection, uuid)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    report = ConnectionIssueReport(
        connection_id=uuid,
        user=payload.user,
        remarks=payload.remarks,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    return {
        "id": report.id,
        "connection_uuid": report.connection_id,
        "user": report.user,
        "remarks": report.remarks,
        "is_solved": report.is_solved,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
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
