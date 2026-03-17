from pydantic import BaseModel

class ObjektOut(BaseModel):
    id: int
    name: str
    adresse: str
    lat: float
    lon: float

    class Config:
        from_attributes = True