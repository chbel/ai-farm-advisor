# AI Farm Advisor

AI-powered precision agriculture platform for Tunisian farmers (olives and tomatoes).
Microservices architecture: FastAPI services behind an API Gateway, PostgreSQL 16 + pgvector.

## Services

| Service | Port (internal) | Database | Status |
|---|---|---|---|
| `api-gateway` | 8000 (published on `127.0.0.1:8000`) | – | Routing to identity, health/readiness |
| `services/identity-service` | 8001 | `identity_db` | Register, login (JWT), Alembic migrations |
| `services/farm-service` | 8002 | `farm_db` | Farms, fields, crops, growth stages, farm journal |
| `services/weather-service` | – | `weather_db` | Planned |
| `services/irrigation-service` | – | `irrigation_db` | Planned |
| `services/disease-detection-service` | – | `disease_db` | Planned |
| `services/ai-advisor-service` | – | `advisor_db` (pgvector) | Planned |

Only the gateway is published to the host. Public API paths: `/api/v1/<service>/...`
(currently `/api/v1/auth/*` → identity `/auth/*`).

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

```powershell
docker compose up -d postgres

# Identity Service
cd services\identity-service
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env            # same password / JWT secret as the root .env
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --port 8001 --reload

# API Gateway (second terminal)
cd api-gateway
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
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

## Tests

Identity and Farm API tests use separate `identity_db_test` / `farm_db_test` databases (created and migrated
automatically; PostgreSQL must be running). The development database is not touched.

```powershell
cd api-gateway;                 .venv\Scripts\python -m pytest
cd services\identity-service;   .venv\Scripts\python -m pytest
cd servicesarm-service;       .venv\Scripts\python -m pytest
```

## Database migrations (per service)

```powershell
cd services\identity-service
.venv\Scripts\python -m alembic revision --autogenerate -m "describe change"
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m alembic check     # fails if models and migrations drift
```

In Docker, the identity container applies pending migrations on startup.

## Conventions

- Configuration via environment variables only; every service ships a `.env.example`.
  `.env` files are git-ignored.
- One database per service; a service never reads another service's database.
- Runtime dependencies in `requirements.txt` (pinned), test tooling in `requirements-dev.txt`.
