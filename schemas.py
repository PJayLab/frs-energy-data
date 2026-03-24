from pydantic import BaseModel, model_validator
from typing import Optional, List
from enum import Enum


class ObjectType(str, Enum):
    building = "building"
    transformer = "transformer"
    distribution_box = "distribution_box"
    disconnect_point = "disconnect_point"


class ObjectCreate(BaseModel):
    name: str
    type: ObjectType
    description: Optional[str] = None
    lat: float
    lon: float


class FeederCreate(BaseModel):
    building_name: str
    transformer_name: str
    distribution_box_name: Optional[str] = None
    disconnect_point_name: Optional[str] = None
    feeder_label: Optional[str]
    fuse_rating: Optional[int]
    notes: Optional[str]


class GPSPoint(BaseModel):
    name: str
    lat: float
    lon: float
    ckw_id: Optional[str] = None
    type: Optional[str] = None  # z. B. "distribution_box", "transformer"

class GPSImportData(BaseModel):
    points: List[GPSPoint]

class ImportData(BaseModel):
    raw_entries: List[dict]
    objects: List[ObjectCreate] = []
    feeders: List[FeederCreate] = []

    @model_validator(mode="after")
    def build_objects_and_feeders(cls, instance):
        objects_map = {}
        feeders_list = []

        for entry in instance.raw_entries:
            building_name = entry["objekt"].strip()
            if building_name not in objects_map:
                objects_map[building_name] = ObjectCreate(name=building_name, type=ObjectType.building)

            dp_name = entry.get("tk_ohne_schalt")
            if dp_name:
                dp_name = dp_name.strip()
                if dp_name and dp_name not in objects_map:
                    objects_map[dp_name] = ObjectCreate(name=dp_name, type=ObjectType.disconnect_point)

            db_name = entry.get("erste_trennstelle")
            if db_name:
                db_name = db_name.strip()
                if db_name and db_name not in objects_map:
                    objects_map[db_name] = ObjectCreate(name=db_name, type=ObjectType.distribution_box)

            tr_name = entry.get("speisung")
            if tr_name:
                tr_name = tr_name.strip()
                if tr_name and tr_name not in objects_map:
                    objects_map[tr_name] = ObjectCreate(name=tr_name, type=ObjectType.transformer)

            feeders_list.append(
                FeederCreate(
                    building_name=building_name,
                    transformer_name=tr_name if tr_name else "",
                    distribution_box_name=db_name if db_name else None,
                    disconnect_point_name=dp_name if dp_name else None,
                    notes="; ".join(entry.get("bemerkungen", [])) if entry.get("bemerkungen") else None
                )
            )

        instance.objects = list(objects_map.values())
        instance.feeders = feeders_list
        return instance