from pydantic import BaseModel
from typing import Optional

class Track(BaseModel):
    id: str
    title: str
    artist: str = ""
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    source_url: str

class SearchResponse(BaseModel):
    tracks: list[Track]

class PrepareRequest(BaseModel):
    url: str

class MediaReady(BaseModel):
    status: str = "ready"
    id: str
    title: str
    artist: str = ""
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    media_url: str
    filename: str
