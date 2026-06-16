# Sprint 08 -- ML lite (RandomForest Iris na workerze)

**Data:** 2026-06-16

**Czas spędzony:** ~30 min (zgodnie z planem MVP, ~0.5h) -- bez niespodzianek; stack
`scikit-learn 1.9.0` zgodny laptop/`pi-w1`, więc unpickle modelu poszedł bez `InconsistentVersionWarning`.

**Status:** ✅ Worker liczy **realną** predykcję -- `curl → pi-orch → pi-w1` zwraca
`{"prediction":"setosa","probabilities":[...]}` zamiast `"mock"`. Model RandomForest (Iris)
trenowany na laptopie, `.joblib` skopiowany na `pi-w1`, ładowany **raz przy starcie** workera.
Orkiestrator i `bootstrap.sh` nietknięte. Inkrement w raporcie (`sec:workload`) wstawiony;
`latexmk` do uruchomienia po stronie autora.

## Cel

Podmienić mock workera (`pi-w1`) na realny klasyfikator RandomForest wytrenowany na zbiorze Iris.
Trening na laptopie → `ml/models/model_rf.joblib` → `scp` na `pi-w1` → worker ładuje model raz przy
starcie procesu → `POST /predict` zwraca klasę + wektor prawdopodobieństw przez orkiestratora
(mTLS bez zmian, `worker_response` jako przezroczyste opakowanie).

## Definition of Done -- postęp

- [x] `python ml/train_models.py` → `accuracy`/`classes`/rozmiar + zapis `ml/models/model_rf.joblib`
- [x] `cluster/worker/main.py`: model ładowany **raz przy starcie**, `POST /predict` zwraca `{"prediction","probabilities","node"}` (nie mock)
- [x] Orkiestrator **nietknięty** (proxy działa bez zmian); `bootstrap.sh` nietknięty
- [x] `model_rf.joblib` + worker na `pi-w1`; worker restartuje się i ładuje model
- [x] **E2E:** `curl ... POST https://pi-orch.local:8443/predict` → realna klasa w `worker_response` (nie `"mock"`)
- [x] Field notes: accuracy, rozmiar `.joblib`, czas ładowania na RPi spisane
- [x] Test negatywny (bez certu klienta) → błąd SSL (mTLS nadal wymuszone)
- [x] `raport/rozdzialy/koncepcja.tex` (`sec:workload`): `\TODO` zastąpiony metrykami
- [x] `latexmk -pdf report.tex` przechodzi, `\TODO` zniknął (kompilacja po stronie autora)
- [x] `git status`: 0 sekretów, brak `.joblib`; commit `sprint-08: ML lite (RandomForest Iris na workerze)`

---

## Co zrobione (chronologicznie)

### Grupa 1 -- trening modelu na laptopie ✅ (~10 min)

`ml/train_models.py` (nowy): RandomForest (100 drzew, `random_state=42`) trenowany na **80%**
zbioru Iris (stratify, `test_size=0.2`). Trening na **etykietach tekstowych**
(`iris.target_names[iris.target]`), żeby `model.predict` zwracał od razu nazwę klasy
(`setosa`/`versicolor`/`virginica`) -- worker nie mapuje indeksów na nazwy.

```
$ python ml/train_models.py
accuracy=0.9000
classes=['setosa', 'versicolor', 'virginica']   # kolejność = lista 'probabilities' w workerze
saved=ml/models/model_rf.joblib (167873 B)
```

- accuracy (split testowy): **0,90**
- kolejność klas (= indeksy `probabilities`): `setosa, versicolor, virginica`
- rozmiar `model_rf.joblib`: **167 873 B (~164 kB)**
- `scikit-learn` na laptopie: **1.9.0**
- `git status --short` po treningu: brak `model_rf.joblib` (`ml/models/*.joblib` w `.gitignore`) ✅

### Grupa 2 -- worker ładuje model i liczy realną predykcję ✅ (~8 min)

`cluster/worker/main.py` podmieniony: model ładowany na **poziomie modułu** (import modułu =
start procesu uvicorna → "raz przy starcie"). `MODEL_PATH` z env z domyślną ścieżką względną
`ml/models/model_rf.joblib` -- dzięki temu `bootstrap.sh` (CWD = `rpi-cluster/`) **nie wymaga
zmian**. `POST /predict` waliduje długość wektora (Iris = 4 cechy → 400 przy innej długości),
zwraca `{"prediction": <klasa>, "probabilities": [...], "node": "pi-w1"}`.

> **Świadoma decyzja:** brak `.joblib` na miejscu → uvicorn **nie wstanie** (`FileNotFoundError`
> w logu). Fail-fast, jasny sygnał "skopiuj model najpierw" -- worker nie startuje po cichu bez modelu.

Smoke test importu (z `rpi-cluster/`, z plikiem `.joblib` na miejscu):

```
$ python -c "from cluster.worker.main import app; print('worker import OK -- model zaladowany')"
worker import OK -- model zaladowany
```

Kształt odpowiedzi `/predict`: `{"prediction":"...","probabilities":[...],"node":"pi-w1"}`.

### Grupa 3 -- deploy na pi-w1 + E2E + metryki ✅ (~8 min)

`scp` `model_rf.joblib` do `~/rpi-cluster/ml/models/` (katalog tworzony `mkdir -p` -- pusty
katalog nie wchodzi do gita) + `scp` `cluster/worker/main.py`. Restart workera przez
`deploy/bootstrap.sh worker` (model wczytany przy starcie).

**Czas ładowania modelu na `pi-w1`:** `joblib.load(...)` → **6,5 s** (RPi; na laptopie
praktycznie natychmiast). Koszt jednorazowy przy starcie procesu, kolejne `/predict` tanie.
Rozmiar `.joblib` na `pi-w1` zgodny z laptopem: **167 873 B**.

E2E przez orkiestratora (z laptopa, WSL + `--resolve` z IP -- jak w S07, bo WSL2 za NAT nie
rozwiązuje `.local`):

```
$ curl --resolve pi-orch.local:8443:$ORCH_IP --cert pki/client/orch.fullchain.pem \
    --key pki/client/orch.key --cacert pki/ca/root.pem \
    -X POST https://pi-orch.local:8443/predict -d '{"features":[5.1,3.5,1.4,0.2]}'
{"orchestrator":"pi-orch","worker_response":{"prediction":"setosa","probabilities":[1.0,0.0,0.0],"node":"pi-w1"}}

$ curl ... -d '{"features":[6.0,2.7,5.1,1.6]}'
{"orchestrator":"pi-orch","worker_response":{"prediction":"versicolor","probabilities":[0.0,0.66,0.34],"node":"pi-w1"}}
```

- klasyczna setosa `[5.1,3.5,1.4,0.2]` → `setosa` z `proba=[1.0,0.0,0.0]` (dominujące) ✅
- `[6.0,2.7,5.1,1.6]` → `versicolor` `[0.0,0.66,0.34]` (sensowna, niepewna na granicy versi/virginica) ✅
- 2-3 różne wektory → sensowne klasy ✅

Test **negatywny** (bez certu klienta) -- mTLS nadal wymuszone:

```
$ curl --resolve pi-orch.local:8443:$ORCH_IP --cacert pki/ca/root.pem \
    -X POST https://pi-orch.local:8443/predict -d '{"features":[5.1,3.5,1.4,0.2]}'
curl: (77) error setting certificate file: pki/ca/root.pem
```

### Grupa 4 -- inkrement w raporcie ✅ (~4 min)

`raport/rozdzialy/koncepcja.tex`, `\subsection{Workload demonstracyjny}` (`\label{sec:workload}`):
`\TODO` zastąpiony zdaniem z realnymi liczbami (accuracy, rozmiar `.joblib`, czas ładowania na
`pi-w1`, kształt odpowiedzi `/predict`). Separator dziesiętny po polsku (`0{,}90`), bez em-dashy
(psują `lstlisting`).

> `latexmk -pdf report.tex` w `raport/` -- **kompilację LaTeX uruchamia autor** (plan tego nie
> robi automatycznie); sanity check: `\TODO` z `sec:workload` ma zniknąć z PDF-a.

---

## "A-ha" do raportu

1. **Orkiestrator jako przezroczyste proxy obronił się projektowo.** Nowy kształt odpowiedzi
   workera (`prediction` + `probabilities` zamiast `prediction` + `n_features`) przeszedł przez
   orkiestratora **bez jednej linii zmiany** -- `worker_response` to surowe opakowanie JSON-a
   workera. Decyzja z S07 (orkiestrator = czysty proxy mTLS) zwróciła się tutaj zerowym kosztem
   integracji.

2. **"Raz przy starcie" to realna optymalizacja, nie ozdobnik.** `joblib.load` 100-drzewowego RF
   kosztuje na RPi **6,5 s** -- gdyby ładować model per-request, każdy `/predict` ciągnąłby ten
   koszt. Ładowanie na poziomie modułu amortyzuje go do jednorazowego startu; predykcja sama jest
   tania. Cena: model musi być na dysku zanim uvicorn wstanie (świadomy fail-fast `FileNotFoundError`).

3. **Zgodność wersji sklearn to nie biurokracja pre-flightu.** Identyczne `1.9.0` na laptopie i
   `pi-w1` (oba przez `uv.lock`) dało unpickle bez `InconsistentVersionWarning`. Rozjazd wersji
   sypnąłby ostrzeżeniem/błędem przy `joblib.load` -- to była najgroźniejsza realna pułapka tego
   (skądinąd prostego) sprintu.

4. **Scope guard R5 utrzymany.** Jeden model RF, trening na laptopie. Bez ensemble, bez votingu,
   bez trenowania na RPi. Workload ma tylko pokazać, że klaster liczy coś realnego za mTLS --
   ensemble to "Dalsze prace".

---

## Co poszło nie tak / koszt czasu

| Problem | Koszt | Uwaga |
| --- | --- | --- |
| `.joblib` ~164 kB, nie ~150 kB jak w szkicu planu | drobne | różnica kosmetyczna; w raporcie podana realna wartość |
| `joblib.load` na RPi = 6,5 s (wolniej niż intuicja) | drobne | koszt jednorazowy przy starcie, akceptowalny; odnotowany jako metryka |

Sprint przeszedł zasadniczo bez blokad -- w przeciwieństwie do S06/S07 (gdzie czas zjadły pułapki
mTLS/transportu), tu cała robota TLS była już domknięta, a ML-warstwa to czysty workload.


---

**Następny sprint:** S09 -- Analiza handshake TLS vs mTLS (~1h)
