import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from cluster.shared.mtls_config import mtls_client

app = FastAPI(title="rpi-mtls-orchestrator")

WORKER_URL = "https://pi-w1.local:8443"
ORCH_CERT = "pki/client/orch.fullchain.pem"
ORCH_KEY = "pki/client/orch.key"
ROOT = "pki/ca/root.pem"

class PredictRequest(BaseModel):
    features: list[float]

@app.get("/health")
def health():
    return {"status": "ok", "node": "pi-orch"}


@app.post("/predict")
def predict(req: PredictRequest):
    with mtls_client(ORCH_CERT, ORCH_KEY, ROOT) as c:
        r = c.post(f"{WORKER_URL}/predict", json=req.model_dump())
    return {"orchestrator": "pi-orch", "worker_response": r.json()}
