# AI Farm Advisor

AI-powered precision agriculture platform for Tunisian farmers (olives and tomatoes).
Microservices architecture: FastAPI services behind an API Gateway, PostgreSQL 16 + pgvector.

## Services

| Service | Port (internal) | Database | Status |
|---|---|---|---|
| `api-gateway` | 8000 (published on `127.0.0.1:8000`) | – | Routing to identity, health/readiness |
| `services/identity-service` | 8001 | `identity_db` | Register, login (JWT), Alembic migrations |
| `services/farm-service` | – | `farm_db` | Planned |
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

## Tests

Identity API tests use a separate `identity_db_test` database (created and migrated
automatically; PostgreSQL must be running). The development database is not touched.

```powershell
cd api-gateway;                 .venv\Scripts\python -m pytest
cd services\identity-service;   .venv\Scripts\python -m pytest
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
