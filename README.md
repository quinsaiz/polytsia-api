# Polytsia API

A REST API for tracking personal media consumption — movies and games with tier list rankings, external metadata
from TMDB and RAWG, and Redis-backed caching.

Built with FastAPI, SQLAlchemy 2, and Docker. Designed as a pet project but structured to production standards.

---

## Features

- **Movie tracking** — search TMDB, add movies to a personal library, set watch status, rating, tier, and notes
- **Game tracking** — search RAWG, add games to a personal library, set play status, rating, tier, and notes
- **Tier lists** — create named tier lists (S/A/B/C/D/F) for movies or games, add items, reorder within tiers
- **External metadata** — proxied search and detail endpoints for TMDB (movies) and RAWG (games) with automatic Redis
  caching
- **Authentication** — JWT access + refresh tokens with rotation, bcrypt password hashing, OAuth2-compatible login flow
- **Pagination** — generic paginated responses with page/page_size/total_pages metadata
- **Redis caching** — search results (1 h), detail pages (24 h), genre/platform catalogs (7 d)
- **Recommendations** — personal movie and game suggestions from Redis candidate pools refreshed by Celery
- **Database migrations** — Alembic with async PostgreSQL support and autogenerate
- **API documentation** — interactive Swagger UI and ReDoc (debug mode only)

---

## Tech Stack

| Layer            | Technology                       |
|------------------|----------------------------------|
| Framework        | FastAPI 0.138+                   |
| ORM              | SQLAlchemy 2 (async) + asyncpg   |
| Validation       | Pydantic v2 + pydantic-settings  |
| Auth             | python-jose (JWT HS256) + bcrypt |
| HTTP client      | httpx (async, connection-pooled) |
| Caching          | Redis 7                          |
| Database         | PostgreSQL 17                    |
| Migrations       | Alembic (async)                  |
| Server           | Uvicorn (ASGI)                   |
| Background jobs  | Celery worker + beat             |
| Containerization | Docker + Docker Compose          |
| Package manager  | uv                               |
| Linting          | Ruff + mypy (strict)             |
| Testing          | pytest + pytest-asyncio + httpx  |

---

## Project Structure

```text
src/
├── main.py              # FastAPI app, lifespan, CORS, router registration
├── config.py            # Pydantic settings (env-based configuration)
├── database.py          # SQLAlchemy engine, session factory, Base
├── dependencies.py      # Shared FastAPI dependencies (HTTP client)
├── pagination.py        # Generic PaginatedResponse[T] and pagination deps
├── redis.py             # Redis connection pool, cache get/set/delete helpers
├── auth/                # User model, JWT service, register/login/refresh/logout
├── movies/              # TMDB proxy, UserMovie model, track/update/delete
├── games/               # RAWG proxy, UserGame model, track/update/delete
├── tierlists/           # TierList + TierListItem models, CRUD with ownership checks
├── profile/             # (planned) user profile features
└── recommendations/     # Candidate collection, bootstrap, and personalized API

alembic/                 # Database migration scripts (async PostgreSQL)
tests/                   # pytest-asyncio integration tests per module
```

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- A `.env` file (see below)

### Environment Variables

Create a `.env` file in the project root based on `.env.example`:

```env
# --- Application Settings ---
APP_NAME=Polytsia
DEBUG=True
SECRET_KEY=your-super-secret-key-change-in-production

# --- Postgres Settings ---
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=polytsia

# --- Redis Settings ---
REDIS_HOST=redis
REDIS_PORT=6379

# --- External Ports ---
POSTGRES_EXTERNAL_PORT=15432
REDIS_EXTERNAL_PORT=16379

# --- JWT Authentication Settings ---
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=30

# --- External APIs Settings ---
TMDB_READ_ACCESS_TOKEN=your-tmdb-read-access-token
RAWG_API_KEY=your-rawg-api-key
```

To obtain API keys:

- **TMDB** — register at [themoviedb.org](https://www.themoviedb.org/) and generate a Read Access Token under API
  settings
- **RAWG** — register at [rawg.io](https://rawg.io/apidocs) and copy your API key

### Run

```bash
docker compose up --build
```

Services started:

| Service        | URL                             |
|----------------|---------------------------------|
| REST API       | <http://localhost:8000/api/v1/> |
| Swagger UI     | <http://localhost:8000/docs>    |
| ReDoc          | <http://localhost:8000/redoc>   |
| Health check   | <http://localhost:8000/health>  |
| PostgreSQL     | localhost:15432                 |
| Redis          | localhost:16379                 |
| Celery worker  | Background candidate refresh    |
| Celery beat    | Daily refresh schedule          |
| Bootstrap job  | Queues missing pools at startup |

Swagger and ReDoc are available only when `DEBUG=True`.

---

## API Overview

### Authentication

| Method | Endpoint                       | Description                          |
|--------|--------------------------------|--------------------------------------|
| POST   | `/api/v1/auth/register`        | Register a new account               |
| POST   | `/api/v1/auth/login`           | Obtain access + refresh tokens       |
| GET    | `/api/v1/auth/me`              | Get current user profile             |
| PATCH  | `/api/v1/auth/me`              | Update email or username             |
| POST   | `/api/v1/auth/refresh`         | Rotate refresh token, get new pair   |
| POST   | `/api/v1/auth/logout`          | Revoke refresh token                 |
| POST   | `/api/v1/auth/change-password` | Change password, revoke all sessions |

For `PATCH /api/v1/auth/me`, omitted fields keep their current values. Explicit `null` for `email` or `username` returns 422.

### Movies

| Method | Endpoint                         | Auth     | Description                          |
|--------|----------------------------------|----------|--------------------------------------|
| GET    | `/api/v1/movies/search`          | No       | Search TMDB by query (cached)        |
| GET    | `/api/v1/movies/genres`          | No       | List TMDB movie genres (cached)      |
| GET    | `/api/v1/movies/{tmdb_id}`       | No       | Get movie details from TMDB (cached) |
| POST   | `/api/v1/movies/track`           | Required | Add movie to personal library        |
| GET    | `/api/v1/movies/`                | Required | List tracked movies (paginated)      |
| PATCH  | `/api/v1/movies/{user_movie_id}` | Required | Update status, rating, tier, notes   |
| DELETE | `/api/v1/movies/{user_movie_id}` | Required | Remove movie from library            |

Movie search accepts `query` and `page` (default 1, range 1–500).

### Games

| Method | Endpoint                       | Auth     | Description                         |
|--------|--------------------------------|----------|-------------------------------------|
| GET    | `/api/v1/games/search`         | No       | Search RAWG by query (cached)       |
| GET    | `/api/v1/games/genres`         | No       | List RAWG game genres (cached)      |
| GET    | `/api/v1/games/platforms`      | No       | List RAWG platforms (cached)        |
| GET    | `/api/v1/games/{rawg_id}`      | No       | Get game details from RAWG (cached) |
| POST   | `/api/v1/games/track`          | Required | Add game to personal library        |
| GET    | `/api/v1/games/`               | Required | List tracked games (paginated)      |
| PATCH  | `/api/v1/games/{user_game_id}` | Required | Update status, rating, tier, notes  |
| DELETE | `/api/v1/games/{user_game_id}` | Required | Remove game from library            |

Game search accepts `query`, `page` (default 1, range 1–100), and `page_size` (default 10, range 1–40).
These ranges are local Polytsia API limits, not confirmed RAWG limits. At `page=100`, `next` is `null` even if RAWG reports another page.
Platforms accepts `page` (default 1). In both responses, `next` and `previous`
are now relative URLs to these API routes, or `null` when RAWG reports no such
page. Previously they were RAWG URLs, which could include the server's API key.
Follow the returned URL on this API to keep the search query and page size.

For tracked movie and game PATCH requests, omitted fields keep their current values. Explicit `null` clears
`personal_rating`, `tier`, or `notes`; `status: null` returns 422. Notes may contain at most 1,000 characters.

### Tier Lists

| Method | Endpoint                                           | Description                      |
|--------|----------------------------------------------------|----------------------------------|
| POST   | `/api/v1/tierlists/`                               | Create a new tier list           |
| GET    | `/api/v1/tierlists/`                               | List all tier lists              |
| GET    | `/api/v1/tierlists/{tier_list_id}`                 | Get tier list with items         |
| DELETE | `/api/v1/tierlists/{tier_list_id}`                 | Delete a tier list               |
| POST   | `/api/v1/tierlists/{tier_list_id}/items`           | Add item to tier list            |
| PATCH  | `/api/v1/tierlists/{tier_list_id}/items/{item_id}` | Move item (change tier/position) |
| DELETE | `/api/v1/tierlists/{tier_list_id}/items/{item_id}` | Remove item from tier list       |

All tier list endpoints require authentication. Items reference tracked movies or games by ID.
Tier list names may contain at most 100 characters.

### Recommendations

All three GET routes require a bearer access token:

| Endpoint                         | Response fields |
|----------------------------------|-----------------|
| `/api/v1/recommendations/`        | `movie_threshold`, `game_threshold`, `is_movies_personalized`, `is_games_personalized`, `movies_pool_available`, `games_pool_available`, `movies`, `games` |
| `/api/v1/recommendations/movies`  | `threshold`, `is_personalized`, `pool_available`, `movies` |
| `/api/v1/recommendations/games`   | `threshold`, `is_personalized`, `pool_available`, `games` |

`movies` contain `tmdb_id`, `title`, `rating`, `overview`, `genre_ids`, and `poster_path`. `games` contain `rawg_id`,
`name`, `rating`, and `background_image`. The `rating` in each result is the catalog rating from TMDB or RAWG. Results
are sorted by this rating, highest first, and limited to 20 per media type.

For each media type, personalization needs at least three tracked records with both a personal rating and a personal
`UserMovie.tier` or `UserGame.tier`. Personal ratings accepted by the update API range from 0 to 10. The threshold is
the tier-weighted average of **personal ratings**: S=5, A=4, B=3, C=2, D=1, F=0.5. Movie personal ratings are
compared directly with TMDB's 0–10 candidate ratings; game personal ratings are divided by two before comparison
with RAWG's 0–5 candidate ratings. A candidate passes when its catalog rating is at least the threshold minus 0.5.
`TierListItem.tier` is only a rank within its own list and does not
affect recommendations; the same tracked title may have different ranks in different lists. Every already tracked
TMDB/RAWG ID is excluded, regardless of status. With fewer than three eligible records, `threshold` is `null`,
`is_personalized` is `false`, and all untracked pool entries may be shown.

Candidate pools are shared between users. Each refresh collects up to 100 unique titles per media type, fetching up
to 20 pages or stopping when the source is exhausted. TMDB discovery sorts by vote average and requests at least
1,000 votes; RAWG sorts by rating and keeps games with at least 500 ratings. A pool may contain fewer than 100 titles.
Redis stores each pool for 26 hours. On `docker compose up --build`, a one-off bootstrap service queues refresh jobs
for missing pools; the Celery worker collects the candidates. This does not delay FastAPI requests while external
catalog pages are fetched. Celery beat refreshes movies daily at 03:00 and games at 03:15 in the configured time zone
(UTC by default). To queue missing pools again, run `docker compose run --rm recommendations_bootstrap`; an existing
pool is left alone. If a pool key is absent or has expired, its route still returns HTTP 200 with an empty result list
and `pool_available: false` (or `movies_pool_available` / `games_pool_available` on the combined route). An existing
pool whose entries are all filtered out returns an empty list with `pool_available: true`. The API does not collect
candidates on demand. A Redis read failure is treated as an unavailable pool by the current cache helper.

---

## Data Model

```text
users
  ├── user_movies      (user_id + tmdb_id unique)
  ├── user_games       (user_id + rawg_id unique)
  ├── refresh_tokens   (hashed, rotated on use)
  └── tier_lists
        └── tier_list_items  (references user_movies XOR user_games)
```

Each tracked movie/game stores: status (`planned` / `watching|playing` / `completed` / `dropped`), personal rating, tier
rank (S--F), and free-text notes.

The tracked record also stores the external catalog rating captured when it was added. This stored external rating
does not determine the recommendation threshold; the user's `personal_rating` and tracking `tier` do.

Tier list items enforce a check constraint ensuring exactly one media reference per item, and uniqueness constraints
prevent duplicates within a list.

---

## Running Tests

Tests use a `polytsia_test` database name, per-test transaction rollback, and httpx `ASGITransport` for in-process
API calls. The name alone does not isolate the PostgreSQL instance. Run tests only with a separate test PostgreSQL
instance and Redis instance: older movie/game fixtures call Redis `FLUSHDB`. Recommendation tests replace the cache
with an in-memory mapping except for the Redis pipeline tests, which require dedicated test services on
`localhost:25432` (PostgreSQL) and `localhost:26379` (Redis), plus `POLYTSIA_TEST_ISOLATED_SERVICES=1`.

```bash
# After starting isolated test services and setting test-only environment variables
pytest tests/recommendations
```

The suite also contains authentication, movie/game tracking, and tier list tests; their Redis fixtures require the
same isolation before running the full suite.
