from sqlalchemy import Column, String, Enum, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
import enum
import base64
import secrets

from frs_energy_data.database import Base


class ObjectType(str, enum.Enum):
    building = "building"
    transformer = "transformer"
    distribution_box = "distribution_box"
    disconnect_point = "disconnect_point"


def gen_short_id(length: int = 12) -> str:
    raw = secrets.token_bytes(9)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:length]


class Object(Base):
    __tablename__ = "objects"

    id = Column(String(12), primary_key=True, default=gen_short_id)
    location = Column(String, nullable=True)
    name = Column(String, nullable=False)
    friendly_name = Column(String, nullable=True)
    type = Column(Enum(ObjectType, name="object_type"), nullable=False)
    description = Column(Text)
    ckw_id = Column(Text)
    geom = Column(Geometry(geometry_type="POINT", srid=4326))


class ServiceConnection(Base):
    """Only relation/connection data between referenced electrical objects."""

    __tablename__ = "service_connections"

    id = Column(String(12), primary_key=True, default=gen_short_id)

    building_id = Column(String(12), ForeignKey("objects.id"), nullable=False)
    transformer_id = Column(String(12), ForeignKey("objects.id"), nullable=False)
    distribution_box_id = Column(String(12), ForeignKey("objects.id"), nullable=True)
    disconnect_point_id = Column(String(12), ForeignKey("objects.id"), nullable=True)

    disconnect_point_outgoing = Column(JSONB, nullable=True)
    source_outgoing = Column(JSONB, nullable=True)
    connection_notes = Column(JSONB, nullable=True)


    building = relationship("Object", foreign_keys=[building_id])
    transformer = relationship("Object", foreign_keys=[transformer_id])
    distribution_box = relationship("Object", foreign_keys=[distribution_box_id])
    disconnect_point = relationship("Object", foreign_keys=[disconnect_point_id])


Feeder = ServiceConnection


class User(Base):
    __tablename__ = "users"

    id = Column(String(12), primary_key=True, default=gen_short_id)
    username = Column(String(128), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
