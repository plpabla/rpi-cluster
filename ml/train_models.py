"""Trening RandomForest na zbiorze Iris -> ml/models/model_rf.joblib.
Uruchamiać na laptopie z katalogu rpi-cluster/:  python ml/train_models.py
(model NIE jest trenowany na RPi -- patrz plan MVP, sekcja 'out of scope').
"""
from pathlib import Path

import joblib
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

OUT = Path("ml/models/model_rf.joblib")

iris = load_iris()
X = iris.data
y = iris.target_names[iris.target]  # etykiety tekstowe: 'setosa', 'versicolor', ...

X_tr, X_te, y_tr, y_te = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

clf = RandomForestClassifier(n_estimators=100, random_state=42)
clf.fit(X_tr, y_tr)
acc = clf.score(X_te, y_te)

OUT.parent.mkdir(parents=True, exist_ok=True)
joblib.dump(clf, OUT)

print(f"accuracy={acc:.4f}")
print(f"classes={list(clf.classes_)}")   # kolejność = kolejność listy 'probabilities' w workerze
print(f"saved={OUT} ({OUT.stat().st_size} B)")