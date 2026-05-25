import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from routes.cc_usage import router as cc_usage_router
from routes.ping import router as ping_router
from routes.rtsp import router as rtsp_router
from routes.spotify import router as spotify_router

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)-9s %(name)s - %(message)s")

app = FastAPI()

OPEN_PATHS = {"/v1/spotify/auth", "/v1/spotify/callback"}


@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    if os.getenv("DEVELOPMENT_MODE", "").lower() in ("1", "true", "yes"):
        return await call_next(request)
    if request.url.path not in OPEN_PATHS:
        expected = os.getenv("API_KEY")
        key = request.headers.get("X-API-Key")
        if not expected or key != expected:
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    return await call_next(request)


app.include_router(cc_usage_router, prefix="/v1")
app.include_router(ping_router, prefix="/v1")
app.include_router(rtsp_router, prefix="/v1")
app.include_router(spotify_router, prefix="/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7333)
