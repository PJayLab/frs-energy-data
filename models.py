from sqlalchemy import Column, Integer, String, Float, ForeignKey
from database import Base

class Trafo(Base):
    __tablename__ = "trafos"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    lat = Column(Float)
    lon = Column(Float)


class Objekt(Base):
    __tablename__ = "objekte"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    adresse = Column(String)
    lat = Column(Float)
    lon = Column(Float)
    trafo_id = Column(Integer, ForeignKey("trafos.id"))


class Verteilkasten(Base):
    __tablename__ = "verteilkasten"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    lat = Column(Float)
    lon = Column(Float)
    trafo_id = Column(Integer)