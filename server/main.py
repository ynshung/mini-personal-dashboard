from fastapi import FastAPI

from routes.cc_usage import router as cc_usage_router

app = FastAPI()

app.include_router(cc_usage_router, prefix="/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3737)
