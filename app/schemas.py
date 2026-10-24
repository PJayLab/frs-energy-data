from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from enum import Enum


class ObjectType(str, Enum):
    building = "building"
    transformer = "transformer"
    distribution_box = "distribution_box"
    disconnect_point = "disconnect_point"


class ObjectCreate(BaseModel):
    location: Optional[str] = None
    name: str
    type: ObjectType
    description: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class ServiceConnectionCreate(BaseModel):
    connection_notes: List[str] = Field(default_factory=list)
    disconnect_point_outgoing: List[str] = Field(default_factory=list)
    source_outgoing: List[str] = Field(default_factory=list)

    building_name: str
    transformer_name: str
    distribution_box_name: Optional[str] = None
    disconnect_point_name: Optional[str] = None



class GPSPoint(BaseModel):
    name: str
    location: Optional[str] = None
    lat: float
    lon: float
    ckw_id: Optional[str] = None
    type: Optional[str] = None


class GPSImportData(BaseModel):
    points: List[GPSPoint]


class ImportData(BaseModel):
    raw_entries: List[dict]
    objects: List[ObjectCreate] = []
    service_connections: List[ServiceConnectionCreate] = []

    @model_validator(mode="after")
    def build_objects_and_service_connections(cls, instance):
        objects_map = {}
        connections_list = []

        for entry in instance.raw_entries:
            building_name = str(entry.get("object") or entry.get("objekt") or "").strip()
            if not building_name:
                continue

            if building_name not in objects_map:
                objects_map[building_name] = ObjectCreate(name=building_name, type=ObjectType.building)

            unswitched_terminal = str(entry.get("unswitched_terminal") or entry.get("tk_ohne_schalt") or "").strip() or None
            if unswitched_terminal and unswitched_terminal not in objects_map:
                objects_map[unswitched_terminal] = ObjectCreate(name=unswitched_terminal, type=ObjectType.disconnect_point)

            first_disconnect = str(entry.get("first_disconnect_point") or entry.get("erste_trennstelle") or "").strip() or None
            if first_disconnect and first_disconnect not in objects_map:
                objects_map[first_disconnect] = ObjectCreate(name=first_disconnect, type=ObjectType.distribution_box)

            source_name = str(entry.get("source_name") or entry.get("speisung") or "").strip() or None
            if source_name and source_name not in objects_map:
                objects_map[source_name] = ObjectCreate(name=source_name, type=ObjectType.transformer)

            notes = entry.get("connection_notes") or entry.get("bemerkungen") or []
            source_outgoing = entry.get("source_outgoing") or entry.get("abgang_speisung") or []
            disconnect_outgoing = entry.get("disconnect_point_outgoing") or entry.get("abgang_trennstelle") or []

            if isinstance(notes, str):
                notes = [notes]
            if isinstance(source_outgoing, str):
                source_outgoing = [source_outgoing]
            if isinstance(disconnect_outgoing, str):
                disconnect_outgoing = [disconnect_outgoing]

            connections_list.append(
                ServiceConnectionCreate(
                    connection_notes=notes,
                    disconnect_point_outgoing=disconnect_outgoing,
                    source_outgoing=source_outgoing,
                    building_name=building_name,
                    transformer_name=source_name or "",
                    distribution_box_name=first_disconnect,
                    disconnect_point_name=unswitched_terminal,
                )
            )

        instance.objects = list(objects_map.values())
        instance.service_connections = connections_list
        return instance


class ConnectionIssueReportCreate(BaseModel):
    user: str
    remarks: str


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
