# Dronnk Backend

Backend separado para Dronnk.

## Flujo Android
1. `GET /api/v1/search?q=...`
2. Al tocar una canción: `POST /api/v1/audio/prepare` con `{ "url": "..." }`
3. La API devuelve `media_url`.
4. Android descarga ese archivo a `Music/Dronnk` y reproduce la URI local.
5. Para video: `POST /api/v1/video/prepare` y luego descargar `media_url` a `Movies/Dronnk`.

## Railway
- Montar volumen en `/data`.
- Variables recomendadas:
  - `DRONNK_DATA_DIR=/data`
  - `DRONNK_COOKIES_FILE=/data/cookies.txt`
- Healthcheck: `/health`

## Local
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

## Nota
Mantén Dronnk separado de TushNH. No reutiliza `/stream`.
