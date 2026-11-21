# AI Farm Advisor

AI-powered precision agriculture platform for Tunisian farmers (olives and tomatoes).
Microservices architecture: FastAPI services behind an API Gateway, PostgreSQL 16 + pgvector.

## Services

| Service | Port (internal) | Database | Status |
|---|---|---|---|
| `api-gateway` | 8000 (published on `127.0.0.1:8000`) | – | Routing, token verification, health/readiness |
| `services/identity-service` | 8001 | `identity_db` | Register, login, profiles, roles, admin |
| `services/farm-service` | 8002 | `farm_db` | Farms, fields, crops, growth stages, farm journal |
| `services/weather-service` | 8003 | – (in-memory cache) | Open-Meteo forecasts, history, ET0, GDD, crop weather alerts |
| `services/irrigation-service` | 8004 | – (stateless) | FAO-56 crop water requirements and irrigation recommendations |
| `services/disease-detection-service` | 8005 | – (model files) | Tomato leaf disease classifier (ONNX), calibrated confidence |
| `services/ai-advisor-service` | – | `advisor_db` (pgvector) | Planned |

Only the gateway is published to the host. Public API paths are `/api/v1/<prefix>/...`:
`auth` and `users` → identity, `farms` → farm, `weather` → weather,
`irrigation` → irrigation, `disease` → disease detection.

## First-time setup

```powershell
Copy-Item .env.example .env            # then set POSTGRES_PASSWORD and JWT_SECRET_KEY
docker compose up -d --build
curl http://127.0.0.1:8000/health/ready
```

The databases are created automatically on an empty volume by
`infrastructure/database/init/01-create-databases.sh`. For an existing volume, run it
manually (idempotent):

```powershell
docker compose exec postgres sh /docker-entrypoint-initdb.d/01-create-databases.sh
```

PostgreSQL is published on `127.0.0.1:5440` (not 5432, which native Windows
PostgreSQL installs often occupy).

## Local development (without Docker for the Python services)

Each service has its own virtual environment and `.env` (copy `.env.example`; use the
same database password and `JWT_SECRET_KEY` as the root `.env`).

```powershell
docker compose up -d postgres

# Example: Identity Service (same pattern for the others)
cd services\identity-service
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.venv\Scripts\python -m alembic upgrade head      # services with a database only
.venv\Scripts\python -m uvicorn app.main:app --port 8001 --reload
```

Ports: gateway 8000, identity 8001, farm 8002, weather 8003, irrigation 8004, disease 8005.

## Authentication

| Method | Path (via gateway) | Access |
|---|---|---|
| POST | `/api/v1/auth/register` | Public. Always creates a `farmer` account |
| POST | `/api/v1/auth/login` | Public. Returns a bearer access token (30 min) |
| GET / PATCH | `/api/v1/users/me` | Authenticated. Read / update name and language |
| POST | `/api/v1/users/me/password` | Authenticated. Change password |
| GET | `/api/v1/users` | Admin. Paginated list (`limit`, `offset`) |
| PATCH | `/api/v1/users/{id}` | Admin. Change `role` or `is_active` |

Send the token as `Authorization: Bearer <token>`. The gateway rejects missing or
invalid tokens before forwarding; each service verifies them again. All read the same
`JWT_SECRET_KEY`.

Roles: `farmer`, `technician`, `consultant`, `admin`. Languages: `fr`, `en`, `aeb`
(Tunisian Arabic). Bootstrap the first administrator from the command line:

```powershell
docker compose exec identity-service python -m app.cli promote-admin you@example.com
```

## Farm Service

All routes require a token. Users see only their own farms (others return 404);
admins see all farms and can filter with `?owner_id=`.

| Path (under `/api/v1`) | Methods |
|---|---|
| `/farms/catalog` | GET: allowed governorates, soils, irrigation methods, crops and stages |
| `/farms`, `/farms/{id}` | POST, GET (paginated) / GET, PATCH, DELETE (cascades) |
| `/farms/{id}/fields`, `.../fields/{id}` | POST, GET / GET, PATCH, DELETE |
| `.../fields/{id}/crops`, `.../crops/{id}` | POST, GET / GET, PATCH, DELETE |
| `.../crops/{id}/stages` | POST (record observation), GET (history) |
| `/farms/{id}/activities`, `.../activities/{id}` | POST, GET (filters: type, field, crop, dates) / DELETE |

Business rules: field areas cannot exceed the farm's declared area; the growth stage
must belong to the crop type; only `planned` crops may have a future planting date;
stage observations and activities cannot be dated in the future. Changing the
current growth stage is done by recording a stage observation (kept as history).

Growth stages:
- Tomato: FAO-56 stages `initial`, `development`, `mid_season`, `late_season`
  (Allen et al., 1998), used later by the Irrigation Service for Kc.
- Olive: simplified BBCH phenology (Sanz-Cortés et al., 2002): `dormancy`,
  `vegetative_growth`, `flowering`, `fruit_set`, `pit_hardening`, `fruit_maturation`.

## Weather Service

Data from [Open-Meteo](https://open-meteo.com/) (CC BY 4.0; attribution is included in
every response). **The free Open-Meteo API is for non-commercial use only.** Before a
commercial launch, subscribe and set `OPEN_METEO_FORECAST_URL` and
`OPEN_METEO_ARCHIVE_URL` (customer endpoints) and `OPEN_METEO_API_KEY`. No code change
is needed.

| Path (under `/api/v1/weather`) | Returns |
|---|---|
| `/current?latitude=&longitude=` | Current conditions |
| `/forecast?latitude=&longitude=&days=1..16&base_temperature=10` | Daily forecast, indicators, period summary |
| `/history?latitude=&longitude=&start_date=&end_date=` | ERA5 reanalysis, max 366 days (latest ~5 days may be missing) |
| `/risks?latitude=&longitude=&days=` | Rule-based alerts |
| `/farms/{farm_id}?days=` | All of the above at a farm's location (ownership checked by the farm service) |

Indicators: FAO-56 Penman-Monteith ET0 (computed by Open-Meteo), growing degree days
(`max(0, (Tmax + Tmin) / 2 - Tbase)`), and water balance (precipitation - ET0).
Alerts: frost, heat stress, heavy rain and strong wind (configurable screening
thresholds), and tomato late blight conditions (Smith Period, Smith 1956). Each alert
states its rule and basis; alerts are indicative, not diagnoses. Responses are cached
in memory (forecast 30 min, history 6 h) per ~1 km cell.

## Irrigation Service

Implements FAO Irrigation and Drainage Paper 56 (Allen et al., 1998).

| Path (under `/api/v1/irrigation`) | Purpose |
|---|---|
| `/reference` | All parameters used, with sources |
| `POST /calculate` | Stateless calculation from explicit crop, field and daily weather inputs |
| `/farms/{farm_id}/fields/{field_id}?days=1..16&stage_progress=0.5` | Recommendation for each active crop on a field, from its recorded growth stage and the forecast |

Method and units:
- ETc = Kc × ET0 (Eq. 56), mm/day. Kc from Table 12: tomato 0.60 / 1.15 / 0.80
  (end range 0.70-0.90); olive 0.65 / 0.70 / 0.70 (for 40-60 % canopy ground cover).
  Kc is interpolated linearly in development and late stages. Optional climate
  adjustment of Kc mid/end (Eq. 62) in `/calculate`.
- Effective rain = fixed fraction of precipitation (default 80 %).
- Net irrigation = max(0, ΣETc - Σeffective rain) over the period; gross = net /
  application efficiency (drip 0.90, sprinkler 0.75, surface 0.60; FAO Training
  Manual 4). Volume: 1 mm on 1 ha = 10 m³; 1 mm on 1 m² = 1 L.
- TAW = 1000 (θFC - θWP) Zr (Eq. 82), RAW = p × TAW (Eq. 83), with Table 19 soil
  midpoints and Table 22 root depth and p (adjusted for ETc). The irrigation interval
  is the number of days until RAW is used; drip fields get daily amounts.

Known limitations (also returned in each result): no soil water carry-over from
before the period; olive Kc overestimates water use in sparse orchards; soil textures
not in FAO-56 Table 19 (e.g. clay loam) get no interval; the farm flow does not yet
apply the climate adjustment (needs daily RHmin and 2 m wind from the Weather Service).

## Disease Detection Service

`POST /api/v1/disease/analyze` (multipart: `image` = JPEG/PNG/WebP up to 8 MB, at least
128 px per side; `crop_type` = `tomato`) returns the top 3 conditions with calibrated
probabilities, an outcome (`likely` or `uncertain`), the model's accuracy on field
photos, and a disclaimer. It never presents a result as a diagnosis and gives no
treatment advice (that is the AI Advisor's job, grounded in sources). Images are
processed in memory and not stored. `GET /api/v1/disease/models` lists installed
models, classes, metrics and training-data licenses.

**Model**: torchvision MobileNetV3-Large (BSD-3-Clause, ImageNet-pretrained),
fine-tuned on 10 tomato classes and served with ONNX Runtime (MIT), so the service needs
no PyTorch (~70 MB RAM). Confidence is calibrated with temperature scaling; predictions
below 60 % or with less than a 15-point margin are reported as `uncertain`.

**Training** (Google Colab, nothing to install locally): open
`ml/disease-detection/train_tomato_colab.ipynb` in Colab, select a T4 GPU runtime,
Run all, then extract the downloaded `tomato_disease_model.zip` into
`services/disease-detection-service/models/tomato/` and restart the service. The
notebook is generated from `train_tomato.py` (`python build_notebook.py`).

**Data** (licenses checked at source): PlantVillage (Mohanty et al., 2016, CC BY-SA
3.0, lab photos) and PlantDoc (Singh et al., 2020, CC BY 4.0, field photos). Accuracy is
reported separately for field photos, the realistic figure for farmers.

**Olive is not supported yet**: no openly licensed olive leaf dataset exists (the
main published one, from Turkey, has no license). The API answers olive requests
with an explanation. Adding a crop needs only a new `models/<crop>/` folder and
catalog entries.

## Tests

Identity and Farm API tests use separate `identity_db_test` / `farm_db_test` databases
(created and migrated automatically; PostgreSQL must be running). The development
databases are not touched. Weather tests need no network: upstreams are mocked.

```powershell
cd api-gateway;                  .venv\Scripts\python -m pytest
cd services\identity-service;    .venv\Scripts\python -m pytest
cd services\farm-service;        .venv\Scripts\python -m pytest
cd services\weather-service;     .venv\Scripts\python -m pytest
cd services\irrigation-service;  .venv\Scripts\python -m pytest
docker build --target test services/disease-detection-service   # runs its tests in Docker
```

## Database migrations (per service)

```powershell
cd services\identity-service     # or services\farm-service
.venv\Scripts\python -m alembic revision --autogenerate -m "describe change"
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m alembic check     # fails if models and migrations drift
```

In Docker, services with a database apply pending migrations on startup.

## Conventions

- Configuration via environment variables only; every service ships a `.env.example`.
  `.env` files are git-ignored.
- One database per service; a service never reads another service's database.
- Runtime dependencies in `requirements.txt` (pinned), test tooling in `requirements-dev.txt`.
