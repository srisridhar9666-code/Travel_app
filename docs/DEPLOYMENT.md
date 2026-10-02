# Deployment and operations

Field Logistics & Travel Management. Two containers — a FastAPI service and an
nginx serving the built SPA — against a MySQL 8 you already run.

---

## 1. Before you deploy anything

The app **refuses to start** with `ENVIRONMENT=production` while any of these is
still at its shipped default. That check lives in `app/core/hardening.py` and is
deliberate: a misconfigured deploy that boots anyway looks healthy and has a
password published in this repository.

| Setting | Generate it with |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `PII_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ADMIN_PASSWORD` | Anything but the documented default |
| `BACKEND_CORS_ORIGINS` | Your real origin. Must not contain `localhost` |
| `FRONTEND_BASE_URL` | Your real origin, over **https** |

Start from `backend/.env.example`, which documents every setting.

> ### Back up `PII_ENCRYPTION_KEY` separately from the database
>
> This is the one irreversible mistake available here. Identity numbers are
> encrypted with that key. A backup containing both the database and the key is
> a backup of neither — and losing the key, or rotating it without re-encrypting,
> turns every stored Aadhaar, PAN and passport number into unrecoverable
> ciphertext. Put it in a password manager or a secret manager, not in the same
> bucket as the dumps.

---

## 2. Running the production shape locally

```bash
docker compose up --build
```

Then http://localhost:8080.

MySQL is deliberately **not** in the compose file — it runs on your host, and
the API reaches it at `host.docker.internal`. Adding a second MySQL would mean
two databases and a confusing afternoon.

What differs from `npm run dev`: no hot reload, the SPA is a production build
served by nginx, and the API sits behind that nginx at `/api` rather than behind
the Vite proxy. Everything else is identical, which is the point — this is the
shape that deploys.

---

## 3. Database

The schema comes from Alembic, never from `create_all`.

```bash
cd backend && alembic upgrade head
```

### Make the ledger append-only

The audit log is hash-chained, so tampering is *detectable*. The grant is what
makes it *impossible* through the application's own credentials. Both matter:
a chain without the grant can be rewritten and re-hashed by anything holding the
database password.

```bash
cd backend && python scripts/grant_append_only.py
```

Then confirm it took, as a system admin:

```
GET /audit/grants   →  {"append_only": true, ...}
```

This needs an application user that is **not** root. If you are still connecting
as root, the grant cannot restrict anything.

### Backups

At minimum, a nightly dump held off-box:

```bash
mysqldump --single-transaction --routines --triggers travel_ops > travel_ops-$(date +%F).sql
```

`--single-transaction` matters: without it the dump locks tables, and a dump
taken mid-write can land between an audit row and the change it describes.

**Test the restore.** A backup nobody has restored is a hypothesis. Restore into
a scratch database and check `GET /audit/verify` still reports `ok: true` — that
confirms the ledger survived the round trip intact.

---

## 4. Operations

### Health

| Endpoint | Tells you |
|---|---|
| `GET /health` | Process is up and MySQL answers a real query; also its version and whether migrations are pending |
| `GET /health/gemini` | Ticket extraction can reach the model, and **which credentials** it used |
| `GET /health/email` | SMTP authenticates, without sending anything |
| `GET /audit/verify` | The hash chain is unbroken (system admin) |
| `GET /audit/grants` | The database itself refuses ledger rewrites (system admin) |

`/health/gemini` reporting the wrong credential source is how you catch a deploy
that silently fell back to the wrong identity.

### Logs

Set `LOG_FORMAT=json` (the default in production) for one JSON object per line,
with `severity` and `request_id` on every record. Every response carries an
`X-Request-ID`, and an inbound one is honoured, so a trace survives the hop from
a load balancer.

To pull one request out of a busy log:

```bash
docker compose logs api | grep '"request_id": "abc123"'
```

### The reminder scheduler

Off unless `SCHEDULER_ENABLED=true`, so a developer pointed at production does
not start mailing people from their laptop.

- `SCHEDULER_MODE=internal` — the loop runs inside the API process. Correct
  whenever at least one instance is always alive.
- `SCHEDULER_MODE=external` — the loop stays stopped and something calls
  `POST /internal/run-reminders` on a schedule. Required on any platform that
  scales to zero.

Reminders are deduplicated by a unique index on `(user_id, channel, dedupe_key)`
where the key names the *event*, not the run. Triggering more often than
necessary is harmless; a traveller cannot be reminded twice about one trip.

### Identity document retention

Purged 90 days after `User.exited_on` — **not** after deactivation, so
suspending someone for a fortnight never starts a deletion clock. The Team page
warns before anything is deleted. A purge empties the record but keeps the row,
so the ledger's references still resolve.

---

## 5. Moving to Cloud Run later

Nothing below needs a code change — all of it is configuration, which is why the
switches exist.

| Concern | What to set |
|---|---|
| **File storage** | `STORAGE_BACKEND=gcs` and `STORAGE_BUCKET=...`. **Required.** The container filesystem is per-instance and dies with the revision, so on local storage an uploaded passport scan survives until the next deploy and no longer. |
| **Scheduler** | `SCHEDULER_MODE=external` plus `SCHEDULER_TOKEN`, then a Cloud Scheduler job calling `POST /internal/run-reminders` with `Authorization: Bearer <token>`. With scale-to-zero there may be no instance alive when a reminder falls due. |
| **Gemini credentials** | Leave `GEMINI_CREDENTIALS_PATH` and `GEMINI_CREDENTIALS_JSON` empty and attach a runtime service account. Application Default Credentials then apply — no key to leak, rotate or bake into an image. |
| **Port** | Already handled: the image honours `$PORT`. |
| **Database** | Cloud SQL via the connector or a private IP. |
| **Secrets** | Secret Manager, surfaced as environment variables. |

### One thing that does *not* carry over cleanly

The rate limiter counts **in memory**, per instance. Three instances means
roughly three times the intended limit. At ~110 users that is unlikely to matter,
and `app/core/ratelimit.py` says so in its own comments — the fix when it does
matter is Redis behind `check()`, not a rewrite. Decide deliberately rather than
discovering it.

For the same reason the API container runs **one worker**. Scale with instances,
not workers: a second worker in the same container would duplicate both the
rate-limit counters and the reminder loop.

---

## 6. When something is wrong

| Symptom | Look at |
|---|---|
| API will not start in production | The startup error lists every unsafe setting by name |
| `Bootstrap admin check failed` | Migrations have not been run — `alembic upgrade head` |
| Uploads vanish after a deploy | `STORAGE_BACKEND=local` on an ephemeral filesystem. See §5 |
| No reminders are going out | `SCHEDULER_ENABLED`, then `SCHEDULER_MODE` — external mode needs an external caller |
| Extraction unavailable | `GET /health/gemini` names the credential source it resolved |
| `/audit/verify` reports a break | The ledger was modified outside the application. The response names the first broken row |
| Port 8000 held after a crash | A wedged uvicorn worker. The "Free port 8000" VS Code task, or `taskkill /PID <pid> /F /T` |
