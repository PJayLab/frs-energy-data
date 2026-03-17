from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Text
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
import enum

from database import Base


# ENUM
class ObjectType(str, enum.Enum):
    building = "building"
    transformer = "transformer"
    distribution_box = "distribution_box"
    disconnect_point = "disconnect_point"


# OBJECTS TABLE
class Object(Base):
    __tablename__ = "objects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(Enum(ObjectType, name="object_type"), nullable=False)
    description = Column(Text)

    # PostGIS geometry (Point)
    geom = Column(Geometry(geometry_type="POINT", srid=4326))


# FEEDERS TABLE (Abgangsliste)
class Feeder(Base):
    __tablename__ = "feeders"

    id = Column(Integer, primary_key=True, index=True)

    building_id = Column(Integer, ForeignKey("objects.id"), nullable=False)
    transformer_id = Column(Integer, ForeignKey("objects.id"), nullable=False)

    distribution_box_id = Column(Integer, ForeignKey("objects.id"), nullable=True)
    disconnect_point_id = Column(Integer, ForeignKey("objects.id"), nullable=True)

    feeder_label = Column(String)  # "1", "1.1"
    fuse_rating = Column(Integer)  # Ampere

    notes = Column(Text)

    # Optional relationships (nice for ORM usage)
    building = relationship("Object", foreign_keys=[building_id])
    transformer = relationship("Object", foreign_keys=[transformer_id])
    distribution_box = relationship("Object", foreign_keys=[distribution_box_id])
    disconnect_point = relationship("Object", foreign_keys=[disconnect_point_id])