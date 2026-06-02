from fastapi import FastAPI

app = FastAPI(title="rpi-mtls-worker")


@app.get("/health")
def health():
    return {"status": "ok", "node": "pi-w1"}
