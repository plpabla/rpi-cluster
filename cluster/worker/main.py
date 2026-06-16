import os

import joblib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="rpi-mtls-worker")

MODEL_PATH = os.environ.get("MODEL_PATH", "ml/models/model_rf.joblib")
_model = joblib.load(MODEL_PATH)


class PredictRequest(BaseModel):
    features: list[float]


@app.get("/health")
def health():
    return {"status": "ok", "node": "pi-w1"}


@app.post("/predict")
def predict(req: PredictRequest):
    if len(req.features) != 4:
        raise HTTPException(status_code=400, detail="Iris: oczekiwano 4 cech")
    X = [req.features]
    label = _model.predict(X)[0]
    proba = _model.predict_proba(X)[0]
    return {
        "prediction": str(label),
        "probabilities": [round(float(p), 4) for p in proba],  
        "node": "pi-w1",
    }