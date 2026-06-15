from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="rpi-mtls-worker")


class PredictRequest(BaseModel):
    features: list[float]


@app.get("/health")
def health():
    return {"status": "ok", "node": "pi-w1"}


@app.post("/predict")
def predict(req: PredictRequest):
    return {"prediction": "mock", "node": "pi-w1", "n_features": len(req.features)}