from __future__ import annotations
import os
import logging
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from .models import SearchResponse, Track, PrepareRequest, MediaReady
from .services.media_service import MediaService, MediaTemporarilyUnavailable, ExternalSourceOnly

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dronnk")

app = FastAPI(title="Dronnk API", version="1.1.0")
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


def upstream_error(prefix: str, exc: Exception) -> HTTPException:
    if isinstance(exc, ExternalSourceOnly):
        return HTTPException(
            status_code=409,
            detail={
                "code": "external_source_only",
                "message": str(exc),
                "retryable": False,
            },
        )
    if isinstance(exc, MediaTemporarilyUnavailable):
        return HTTPException(
            status_code=503,
            detail={
                "code": "youtube_temporarily_unavailable",
                "message": str(exc),
                "retryable": True,
            },
            headers={"Retry-After": "45"},
        )
    return HTTPException(status_code=502, detail=f"{prefix}: {exc}")

@app.get("/health")
def health():
    return {"status": "ok", "service": "dronnk-api", "version": "1.1.0"}

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
        raise upstream_error("No se pudo preparar el audio", exc)

@app.post("/api/v1/video/prepare", response_model=MediaReady)
def prepare_video(payload: PrepareRequest, request: Request):
    try:
        result = media.prepare_video(payload.url)
        filename = result["filename"]
        return MediaReady(**result, media_url=f"{public_base(request)}/media/video/{quote(filename)}")
    except Exception as exc:
        logger.exception("video prepare failed")
        raise upstream_error("No se pudo preparar el video", exc)

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


# -------------------------------------------------------------------------
# Compatibilidad con Dronnk Android v1.0 inicial
# El primer APK todavía usa las rutas/respuestas heredadas de TushNH.
# Se mantienen como aliases mientras migramos el cliente a /api/v1/*.
# -------------------------------------------------------------------------
@app.get("/buscar")
def legacy_search(request: Request, termino: str = Query(min_length=1), limit: int = 30):
    try:
        items = media.search(termino.strip(), limit)
        canciones = []
        for item in items:
            canciones.append({
                "id": item.get("id"),
                "titulo": item.get("title") or "Canción",
                "url": item.get("source_url"),
                "thumbnail": item.get("thumbnail"),
                "duracion": str(item.get("duration")) if item.get("duration") is not None else None,
                "canal": item.get("artist") or "",
                "archivo": None,
                "isFavorite": False,
                "isDownloaded": False,
                "localPath": None,
            })
        return {"canciones": canciones}
    except Exception as exc:
        logger.exception("legacy search failed")
        raise HTTPException(status_code=502, detail=f"No se pudo buscar: {exc}")


@app.get("/descargar")
def legacy_download(request: Request, url: str = Query(min_length=1)):
    try:
        result = media.prepare_audio(url)
        filename = result["filename"]
        media_url = f"{public_base(request)}/media/audio/{quote(filename)}"
        duration = result.get("duration")
        return {
            "status": "success",
            "url": media_url,
            "titulo": result.get("title"),
            "archivo": filename,
            "thumbnail": result.get("thumbnail"),
            "canal": result.get("artist") or "",
            "duracion": str(duration) if duration is not None else None,
            "message": None,
        }
    except Exception as exc:
        logger.exception("legacy download failed")
        raise upstream_error("No se pudo descargar la canción", exc)


@app.get("/descargar-video")
def legacy_download_video(request: Request, url: str = Query(min_length=1)):
    try:
        result = media.prepare_video(url)
        filename = result["filename"]
        media_url = f"{public_base(request)}/media/video/{quote(filename)}"
        duration = result.get("duration")
        return {
            "status": "success",
            "url": media_url,
            "titulo": result.get("title"),
            "archivo": filename,
            "thumbnail": result.get("thumbnail"),
            "canal": result.get("artist") or "",
            "duracion": str(duration) if duration is not None else None,
            "message": None,
        }
    except Exception as exc:
        logger.exception("legacy video download failed")
        raise upstream_error("No se pudo descargar el video", exc)
