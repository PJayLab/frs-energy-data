from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Text
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
    """Electricity connection from transformer-side network to a building."""

    __tablename__ = "service_connections"

    id = Column(String(12), primary_key=True, default=gen_short_id)

    building_id = Column(String(12), ForeignKey("objects.id"), nullable=False)
    transformer_id = Column(String(12), ForeignKey("objects.id"), nullable=False)
    distribution_box_id = Column(String(12), ForeignKey("objects.id"), nullable=True)
    disconnect_point_id = Column(String(12), ForeignKey("objects.id"), nullable=True)

    municipality = Column(String, nullable=True)  # Gemeinde
    object_name = Column(String, nullable=True)  # Objekt
    insurance_number = Column(String, nullable=True)  # Assek. Nr.
    unswitched_terminal = Column(String, nullable=True)  # TK ohne Schaltmöglichkeit
    first_disconnect_point_name = Column(String, nullable=True)  # Erste Trennstelle
    source_name = Column(String, nullable=True)  # Speisung

    disconnect_point_outgoing = Column(JSONB, nullable=True)  # Abgang Trennstelle
    source_outgoing = Column(JSONB, nullable=True)  # Abgang Speisung
    connection_notes = Column(JSONB, nullable=True)  # Bemerkungen/Verbindung

    fuse_rating = Column(Integer)

    building = relationship("Object", foreign_keys=[building_id])
    transformer = relationship("Object", foreign_keys=[transformer_id])
    distribution_box = relationship("Object", foreign_keys=[distribution_box_id])
    disconnect_point = relationship("Object", foreign_keys=[disconnect_point_id])


# Backwards-compatible alias in case older modules still import Feeder.
Feeder = ServiceConnection
