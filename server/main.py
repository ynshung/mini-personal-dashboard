from dotenv import load_dotenv
from fastapi import FastAPI

from routes.cc_usage import router as cc_usage_router
from routes.spotify import router as spotify_router

load_dotenv()

app = FastAPI()

app.include_router(cc_usage_router, prefix="/v1")
app.include_router(spotify_router, prefix="/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7333)
