# User Service

Handles user registration, authentication (RS256 JWT), profile management, and role-based access control (RBAC) for the AI e-commerce platform.

**Port:** `8001` | **Database:** `users_db` (PostgreSQL) | **Cache:** Redis

---

## Architecture

### Internal layer breakdown

```mermaid
graph TD
    subgraph Client
        C[HTTP Client / API Gateway]
    end

    subgraph UserService["User Service (FastAPI — port 8001)"]
        direction TB
        MW[CORS + Prometheus Middleware]
        subgraph Routers
            AR["/auth router"]
            UR["/users router"]
        end
        subgraph Dependencies
            GCU[get_current_user]
            ADM[require_admin]
            VND[require_vendor]
            GR[get_redis]
            GDB[get_db]
        end
        subgraph Services
            AS[auth_service]
            USS[user_service]
        end
        subgraph Models
            UM[User ORM]
            UPM[UserProfile ORM]
        end
    end

    subgraph DataStores
        PG[(PostgreSQL<br/>users_db)]
        RD[(Redis)]
    end

    C --> MW --> AR & UR
    AR & UR --> GCU & GR & GDB
    GCU --> GDB
    AR --> AS
    UR --> USS
    AS & USS --> UM & UPM
    UM & UPM --> PG
    AS --> RD
    GR --> RD
```

### Authentication flow (JWT RS256)

```mermaid
sequenceDiagram
    participant C  as Client
    participant US as User Service
    participant DB as PostgreSQL
    participant RD as Redis

    C->>US: POST /auth/register {email, password}
    US->>DB: INSERT user + profile
    US-->>C: 201 UserResponse

    C->>US: POST /auth/login {email, password}
    US->>DB: SELECT user WHERE email=?
    US->>US: bcrypt.checkpw(password, hash)
    US->>US: jwt.encode(payload, private_key, RS256)
    US->>RD: SETEX refresh:{sha256(token)} → user_id (TTL 7d)
    US-->>C: 200 {access_token, refresh_token}

    Note over C,US: access_token expires in 30 min

    C->>US: POST /auth/refresh {refresh_token}
    US->>US: jwt.decode(token, public_key, RS256)
    US->>RD: GET refresh:{sha256(token)}
    RD-->>US: user_id
    US->>RD: DEL refresh:{sha256(old_token)}  (rotation)
    US->>US: Mint new access + refresh tokens
    US->>RD: SETEX refresh:{sha256(new_token)} → user_id
    US-->>C: 200 {new_access_token, new_refresh_token}

    C->>US: POST /auth/logout {refresh_token}
    US->>RD: DEL refresh:{sha256(token)}
    US-->>C: 200 {message: "Successfully logged out."}
```

### Cross-service JWT verification

```mermaid
flowchart LR
    subgraph Other["Other Service (e.g. Product Service)"]
        OS[Fetch public key on startup]
        OV[jwt.decode token with public key]
    end

    subgraph US["User Service"]
        PK["GET /auth/public-key → {public_key: '...'} "]
    end

    OS -->|"GET /auth/public-key"| PK
    PK --> OV
```

### Password reset flow

```mermaid
sequenceDiagram
    participant C  as Client
    participant US as User Service
    participant DB as PostgreSQL
    participant RD as Redis
    participant SMTP as Email (SMTP)

    C->>US: POST /auth/forgot-password {email}
    US->>DB: SELECT user WHERE email=?
    alt user exists
        US->>RD: SETEX pwd_reset:{token} → user_id (TTL 1h)
        US->>SMTP: Send reset link (token in URL)
    end
    US-->>C: 200 (always, prevents enumeration)

    C->>US: POST /auth/reset-password {token, new_password}
    US->>RD: GET pwd_reset:{token}
    RD-->>US: user_id
    US->>DB: UPDATE user SET hashed_password=bcrypt(new_password)
    US->>RD: DEL pwd_reset:{token}
    US-->>C: 200 {message: "Password has been reset."}
```

---

## API Endpoints

### Auth — `/auth`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | None | Create account → `201 UserResponse` |
| POST | `/auth/login` | None | Email + password → `TokenResponse` |
| POST | `/auth/refresh` | None | Rotate refresh token → `TokenResponse` |
| POST | `/auth/logout` | None | Revoke refresh token |
| POST | `/auth/forgot-password` | None | Initiate reset (always 200) |
| POST | `/auth/reset-password` | None | Consume reset token, set new password |
| GET | `/auth/public-key` | None | RS256 public key for other services |

### Users — `/users`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/users/me` | Bearer | Authenticated user's data |
| GET | `/users/me/profile` | Bearer | Authenticated user's profile |
| PATCH | `/users/me/profile` | Bearer | Update own profile |
| GET | `/users/` | Admin | List all users (paginated) |
| GET | `/users/{user_id}` | Admin | Fetch any user by UUID |

### System

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | `{"status":"ok","service":"user-service"}` |
| GET | `/metrics` | None | Prometheus metrics |
| GET | `/docs` | None | Swagger UI (non-production only) |

---

## Data Models

```mermaid
erDiagram
    USERS {
        uuid        id          PK
        varchar     email       UK
        varchar     hashed_password
        boolean     is_active
        boolean     is_verified
        enum        role        "customer|admin|vendor"
        timestamptz created_at
        timestamptz updated_at
    }

    USER_PROFILES {
        uuid        id          PK
        uuid        user_id     FK
        varchar     full_name
        varchar     phone
        varchar     avatar_url
        json        addresses
        timestamptz created_at
        timestamptz updated_at
    }

    USERS ||--|| USER_PROFILES : "has one"
```

---

## Project Structure

```
user-service/
├── app/
│   ├── config.py          # Pydantic Settings (reads RSA key files)
│   ├── database.py        # Async engine, SessionLocal, TimestampMixin, get_db
│   ├── redis_client.py    # Connection pool lifecycle
│   ├── dependencies.py    # get_current_user, require_admin, require_vendor
│   ├── exceptions.py      # Domain exceptions + FastAPI handlers
│   ├── main.py            # App factory, lifespan, CORS, Prometheus
│   ├── models/
│   │   └── user.py        # User, UserProfile, UserRole
│   ├── schemas/
│   │   └── user.py        # All Pydantic v2 request/response schemas
│   ├── services/
│   │   ├── auth_service.py   # bcrypt, JWT, Redis token store
│   │   └── user_service.py   # Profile CRUD, paginated listing
│   └── routers/
│       ├── auth.py        # /auth/* endpoints
│       └── users.py       # /users/* endpoints
├── alembic/               # Async migration environment
│   ├── env.py
│   ├── script.py.mako
│   └── versions/          # Migration scripts (generated by alembic revision)
├── tests/
│   ├── conftest.py        # RSA keys, SQLite engine, mock Redis, client fixture
│   └── test_auth.py       # 20 integration tests for /auth
├── scripts/
│   └── generate_keys.py   # One-shot RSA key pair generator
├── Dockerfile
├── alembic.ini
├── pytest.ini
├── requirements.txt
└── .env.example
```

---

## Setup

### 1. Generate RSA key pair

```bash
python scripts/generate_keys.py
# Writes keys/private.pem (secret) and keys/public.pem
```

Add `keys/` to `.gitignore` so the private key is never committed.

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set DATABASE_URL, REDIS_URL, SMTP credentials, etc.
```

### 3. Run with Docker

```bash
docker build -t user-service .
docker run -p 8001:8001 --env-file .env user-service
```

### 4. Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

### 5. Apply database migrations

```bash
# Create a new migration after model changes:
alembic revision --autogenerate -m "describe change"

# Apply all pending migrations:
alembic upgrade head

# Roll back one migration:
alembic downgrade -1
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host:5432/users_db` |
| `REDIS_URL` | — | `redis://localhost:6379/0` |
| `JWT_PRIVATE_KEY_PATH` | `keys/private.pem` | Path to RSA private key (PEM) |
| `JWT_PUBLIC_KEY_PATH` | `keys/public.pem` | Path to RSA public key (PEM) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime (Redis TTL) |
| `SMTP_HOST` | `localhost` | SMTP server host |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_FROM` | — | Sender address for password-reset emails |
| `ENVIRONMENT` | `development` | `development` / `test` / `production` |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |

---

## Testing

Tests use SQLite in-memory (via `aiosqlite` + `StaticPool`) and a fully mocked Redis — no external services required.

```bash
# Install deps
pip install -r requirements.txt

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test class
pytest tests/test_auth.py::TestRegister -v
```

Test coverage areas:

- Registration: success, duplicate email, weak passwords (missing upper/lower/digit/length)
- Login: success (Redis setex called), wrong password, unknown email
- Token refresh: success with rotation, revoked token, invalid token
- Logout: success (Redis delete called), idempotent (already-revoked token)
- Password reset: forgot (existing + unknown email), reset with invalid/valid token
- Public key endpoint (no auth required)
- Health check

---

## Security Notes

- Passwords hashed with **bcrypt** at cost factor 12.
- JWTs signed with **RS256** (asymmetric) — the private key never leaves this service.
- Refresh tokens are stored as `sha256(token)` hashes in Redis, never the raw token.
- Refresh token **rotation** is enforced: consuming a refresh token immediately deletes it and issues a new pair.
- The `forgot-password` endpoint always returns `200 OK` regardless of whether the email exists, preventing user enumeration.
- Swagger UI (`/docs`) is disabled in `production` environment.
