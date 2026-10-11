# Wallet-Based Per-Minute Session Billing

A Django + DRF backend for paid 1:1 consultation sessions. It covers:

- prepaid wallets
- a mock recharge with an idempotent webhook
- a session state machine
- per-minute billing
- automatic session end when the balance runs out

- Design, state diagram and ER diagram: [docs/DESIGN.md](docs/DESIGN.md)

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

SQLite is used by default. To use PostgreSQL:

```bash
pip install "psycopg[binary]"
export DB_ENGINE=postgres DB_NAME=wallet_billing DB_USER=postgres DB_PASSWORD=... DB_HOST=localhost DB_PORT=5432
python manage.py migrate
```

### With Docker (PostgreSQL + API + billing worker)

```bash
docker compose up --build          # API on http://127.0.0.1:8000
WEB_PORT=8001 docker compose up    # use another host port if 8000 is taken
docker compose run --rm web python manage.py test   # run the tests against PostgreSQL
docker compose down -v             # stop and delete the database volume
```

`docker compose up` starts three services:

- `db`: PostgreSQL 17
- `web`: runs migrations, then serves the API with gunicorn
- `billing`: runs `bill_active_sessions` every 60 seconds, so in-progress sessions are billed and auto-ended without manual calls

To build only the image: `docker build -t wallet-billing .`

| Env var | Default | Purpose |
|---|---|---|
| `RECHARGE_WEBHOOK_SECRET` | `dev-webhook-secret` | Shared secret the mock gateway sends as `X-Webhook-Secret` |
| `DJANGO_SECRET_KEY` | dev key | Django secret |
| `DJANGO_DEBUG` | `1` | Debug mode |

## Running tests

```bash
python manage.py test
```

There are 34 tests. The six required scenarios are in
[tests/test_required_scenarios.py](tests/test_required_scenarios.py):

| # | Scenario | Test |
|---|---|---|
| 1 | Less than 5 minutes of balance cannot start | `test_1_user_with_less_than_five_minutes_of_balance_cannot_start` |
| 2 | Happy path: recharge → create → accept → start → bill 2 min → end | `test_2_happy_path_...` |
| 3 | Balance runs out mid-session | `test_3_session_auto_ends_when_wallet_cannot_cover_next_minute` |
| 4 | Invalid transition returns 400 | `test_4_ending_a_session_before_it_started_returns_400` |
| 5 | Duplicate recharge callback credits once | `test_5_duplicate_success_callback_credits_wallet_once` |
| 6 | Duplicate billing charges once | `test_6_billing_the_same_minute_twice_charges_once` |

[tests/test_edge_cases.py](tests/test_edge_cases.py) covers these additional cases:

- the state machine table itself
- one active session per user, checked in the app and in the DB
- the rate snapshot being fixed at start
- provider end, reject and cancel
- late billing and partial minutes
- role permissions
- the webhook secret and failed callbacks
- DB constraints
- ledger consistency
- the billing management command

Tests freeze `django.utils.timezone.now`, so "2 minutes later" runs instantly.

## API

All endpoints except register, token and the webhook need `Authorization: Token <key>`.

| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/api/auth/register/` | anyone | `{username, password, role: USER\|PROVIDER, per_minute_rate}` → returns token |
| POST | `/api/auth/token/` | anyone | `{username, password}` → `{token}` |
| GET | `/api/auth/me/` | any | current user |
| GET | `/api/wallet/` | any | current balance |
| GET | `/api/wallet/transactions/` | any | ledger |
| GET/POST | `/api/wallet/recharges/` | USER | POST `{amount}` → pending recharge with `reference` |
| POST | `/api/wallet/recharges/callback/` | gateway | header `X-Webhook-Secret`; `{reference, status: success\|failed}` |
| GET/POST | `/api/sessions/` | USER creates | POST `{provider_id?}` (with a provider it is assigned immediately) |
| GET | `/api/sessions/{id}/` | participants | includes `allowed_actions` |
| POST | `/api/sessions/{id}/assign/` | USER | `{provider_id}` |
| POST | `/api/sessions/{id}/accept/` | assigned PROVIDER | |
| POST | `/api/sessions/{id}/reject/` | assigned PROVIDER | back to `unassigned` |
| POST | `/api/sessions/{id}/start/` | USER | needs balance ≥ 5 × rate |
| POST | `/api/sessions/{id}/bill/` | participants | billing tick, safe to repeat |
| POST | `/api/sessions/{id}/end/` | USER or PROVIDER | bills completed minutes, then completes |
| POST | `/api/sessions/{id}/cancel/` | USER or PROVIDER | only before start |

Business errors return JSON like `{"error": "<code>", "detail": "<message>"}`:

- **400** for `invalid_transition`, `insufficient_balance`, `active_session_exists`, …
- **403** for the wrong role or wrong participant.
- **404** for a session that isn't yours.

### Periodic billing

```bash
python manage.py bill_active_sessions   # schedule every minute (cron / Task Scheduler)
```

### Walkthrough (curl)

```bash
B=http://127.0.0.1:8000/api
U=$(curl -s -XPOST $B/auth/register/ -H 'Content-Type: application/json' \
     -d '{"username":"alice","password":"pass12345","role":"USER"}' | jq -r .token)
P=$(curl -s -XPOST $B/auth/register/ -H 'Content-Type: application/json' \
     -d '{"username":"drbob","password":"pass12345","role":"PROVIDER","per_minute_rate":"50"}' | jq -r .token)

REF=$(curl -s -XPOST $B/wallet/recharges/ -H "Authorization: Token $U" -d amount=1000 | jq -r .reference)
curl -s -XPOST $B/wallet/recharges/callback/ -H 'X-Webhook-Secret: dev-webhook-secret' \
     -d reference=$REF -d status=success

PID=$(curl -s $B/auth/me/ -H "Authorization: Token $P" | jq .id)
SID=$(curl -s -XPOST $B/sessions/ -H "Authorization: Token $U" -d provider_id=$PID | jq .id)
curl -s -XPOST $B/sessions/$SID/accept/ -H "Authorization: Token $P"
curl -s -XPOST $B/sessions/$SID/start/  -H "Authorization: Token $U"
# ...wait a few minutes...
curl -s -XPOST $B/sessions/$SID/bill/   -H "Authorization: Token $U"
curl -s -XPOST $B/sessions/$SID/end/    -H "Authorization: Token $U"
```

## Project layout

```
config/          settings, root urls
core/            DomainError types + DRF exception handler, role permissions
accounts/        custom User (role, per_minute_rate), register/token endpoints
wallet/          Wallet, RechargeTransaction, WalletTransaction ledger; services; webhook
consultations/   ConsultationSession, state_machine.py, services.py (start/end/bill), API, cron command
tests/           required scenarios + edge cases
docs/DESIGN.md   state diagram, ER diagram, implementation decisions
```
