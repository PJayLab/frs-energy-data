from pydantic import BaseModel
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


class ImportData(BaseModel):
    objects: List[ObjectCreate]
    feeders: List[FeederCreate]