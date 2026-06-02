import httpx
from fastapi import FastAPI

app = FastAPI(title="rpi-mtls-orchestrator")

WORKER_URL = "http://pi-w1.local:8000"


@app.get("/health")
def health():
    return {"status": "ok", "node": "pi-orch"}


@app.get("/test-worker")
def test_worker():
    r = httpx.get(f"{WORKER_URL}/health", timeout=5.0)
    return {"orchestrator": "ok", "worker_response": r.json()}
