from __future__ import annotations
import os
import logging
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from .models import SearchResponse, Track, PrepareRequest, MediaReady
from .services.media_service import MediaService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dronnk")

app = FastAPI(title="Dronnk API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
media = MediaService()


def public_base(request: Request) -> str:
    configured = os.getenv("DRONNK_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")

@app.get("/health")
def health():
    return {"status": "ok", "service": "dronnk-api", "version": "1.0.0"}

@app.get("/api/v1/search", response_model=SearchResponse)
def search(q: str = Query(min_length=1), limit: int = 30):
    try:
        tracks = [Track(**x) for x in media.search(q.strip(), limit)]
        return SearchResponse(tracks=tracks)
    except Exception as exc:
        logger.exception("search failed")
        raise HTTPException(status_code=502, detail=f"No se pudo buscar: {exc}")

@app.post("/api/v1/audio/prepare", response_model=MediaReady)
def prepare_audio(payload: PrepareRequest, request: Request):
    try:
        result = media.prepare_audio(payload.url)
        filename = result["filename"]
        return MediaReady(**result, media_url=f"{public_base(request)}/media/audio/{quote(filename)}")
    except Exception as exc:
        logger.exception("audio prepare failed")
        raise HTTPException(status_code=502, detail=f"No se pudo preparar el audio: {exc}")

@app.post("/api/v1/video/prepare", response_model=MediaReady)
def prepare_video(payload: PrepareRequest, request: Request):
    try:
        result = media.prepare_video(payload.url)
        filename = result["filename"]
        return MediaReady(**result, media_url=f"{public_base(request)}/media/video/{quote(filename)}")
    except Exception as exc:
        logger.exception("video prepare failed")
        raise HTTPException(status_code=502, detail=f"No se pudo preparar el video: {exc}")

@app.get("/media/audio/{filename}")
def get_audio(filename: str):
    path = media.audio_dir / Path(filename).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio no encontrado")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)

@app.get("/media/video/{filename}")
def get_video(filename: str):
    path = media.video_dir / Path(filename).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video no encontrado")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
