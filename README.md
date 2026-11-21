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
| `services/irrigation-service` | – | `irrigation_db` | Planned |
| `services/disease-detection-service` | – | `disease_db` | Planned |
| `services/ai-advisor-service` | – | `advisor_db` (pgvector) | Planned |

Only the gateway is published to the host. Public API paths are `/api/v1/<prefix>/...`:
`auth` and `users` → identity, `farms` → farm, `weather` → weather.

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

Ports: gateway 8000, identity 8001, farm 8002, weather 8003.

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

## Tests

Identity and Farm API tests use separate `identity_db_test` / `farm_db_test` databases
(created and migrated automatically; PostgreSQL must be running). The development
databases are not touched. Weather tests need no network: upstreams are mocked.

```powershell
cd api-gateway;                  .venv\Scripts\python -m pytest
cd services\identity-service;    .venv\Scripts\python -m pytest
cd services\farm-service;        .venv\Scripts\python -m pytest
cd services\weather-service;     .venv\Scripts\python -m pytest
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
