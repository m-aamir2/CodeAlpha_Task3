# ConnectX

ConnectX is a responsive meeting workspace built with React, TypeScript, Django REST Framework, Django Channels, and LiveKit. It supports scheduled and instant meetings, password protected rooms, waiting rooms, real-time chat, protected file sharing, a collaborative whiteboard, and meeting history.

Run `npm run dev`, then open the frontend URL printed by the script.

## Features

- JWT registration, email or username login, refresh and logout; profile photo and bio; password change and reset.
- Meeting creation, scheduling, password protection, multi-address invite emails, join by code or link, external Google Meet/Zoom/Teams links, waiting room, participant roles, moderation, and host end controls.
- Pre-join camera and microphone preview, device selection, local mute and camera controls, screen sharing, speaker selection, and responsive participant grid.
- LiveKit SFU media with server generated, room scoped tokens. The backend validates the member before issuing a token.
- Stored meeting chat, participant events, notifications, and whiteboard collaboration over authenticated Channels WebSockets.
- Authorized document, spreadsheet, presentation, image, text, CSV, and ZIP uploads with extension and file-signature checks, size limits, authenticated PDF page previews, private downloads, and optional S3 compatible storage.
- Participant records and lists are not capped at 100; LiveKit room tokens do not set a participant limit.
- Canvas whiteboard with pen, eraser, text, color and size controls, undo, clear, PNG export, collaboration, and stored snapshots.
- Searchable meeting list, upcoming and history views, date filter, participant history, reminder notifications, and reuse of meeting settings.
- Dark meeting room and light workspace, mobile layouts, loading and empty states, toasts, and confirmation prompts.

## Stack

| Layer | Technology |
|---|---|
| Web app | React 19, TypeScript, Vite 7, Tailwind CSS 3, React Router, TanStack Query, Zustand, Axios, Lucide |
| API | Python 3.12+, Django 5.2, Django REST Framework, Simple JWT |
| Real time | Django Channels, Daphne ASGI, Channels Redis in production |
| Media | LiveKit SFU, LiveKit Python API, LiveKit React client components, WebRTC |
| Database | SQLite for local development; PostgreSQL supported |
| File storage | Local private storage by default; S3 compatible storage supported |

`backend/requirements.txt` and `frontend/package-lock.json` pin the runtime dependencies. Python 3.12 and Node.js 20 or newer are used by the local startup script.

## Architecture

```text
React / Vite
  ├── HTTPS REST + JWT ───────────── Django REST Framework
  ├── WSS app events + JWT ───────── Django Channels / Daphne ── Redis (production)
  └── WSS media + room token ─────── LiveKit SFU
                                       └── WebRTC encrypted media transport
                                      Django ── SQLite / PostgreSQL
                                      Django ── private disk / S3 compatible storage
```

The app WebSocket carries chat, presence, participant status, notifications, and whiteboard state. LiveKit carries audio, video, and screen-share tracks. These are separate transports. The database models are in `backend/core/models.py`: user profile, meeting, meeting participant, meeting message, shared file, whiteboard snapshot, and notification. Meeting history is represented by the meeting and participant timestamps and status; it is not duplicated in a second history table.

LiveKit media uses WebRTC's encrypted transport. ConnectX does **not** claim end-to-end encryption: the SFU routes media. ConnectX does not record meetings.

## Run locally

From this directory:

```bash
npm run dev
```

The script checks Python and Node, creates `backend/.venv`, installs pinned backend dependencies and frontend dependencies when missing or out of date, creates `.env` with a fresh local Django secret when needed, runs migrations and Django checks, starts the bundled LiveKit development server, Daphne, Vite, and the scheduled reminder process, and waits for services to respond. It chooses the first free port starting at 8000 for the API and 5173 for the frontend, then prints the actual URLs. Set `BACKEND_PORT` or `FRONTEND_PORT` before starting to choose a different starting port.

- Web app, API, and health URLs: use the addresses printed by `npm run dev`.
- Stop services: `npm run stop`
- Logs and process IDs: `.run/`

SQLite, the in-memory Channels layer, and Django's console email backend work without extra services. The local LiveKit development server runs in Docker. Use a LiveKit Cloud or secured self-hosted configuration for production and multi-device use; the local `devkey` and `secret` are for development only.

### LiveKit local development

On Linux, `./start.sh` starts the pinned official LiveKit Server container from `docker-compose.yml` when `.env` has the local defaults. It uses LiveKit development mode and the credentials shown in [LiveKit's local run guide](https://docs.livekit.io/transport/self-hosting/local/). You can also start it directly with:

```bash
livekit-server --dev
```

Development mode uses the API key `devkey`, secret `secret`, and WebSocket URL `ws://localhost:7880`. Add these values to the root `.env`:

```dotenv
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
```

The `.env.example` file already contains these local values. `./stop.sh` stops the development server with the app. Docker Compose uses host networking for LiveKit's local media ports. Browser camera and microphone access requires localhost or HTTPS.

For a hosted setup, create a LiveKit Cloud project and use its `wss://` project URL and server API credentials. Keep the API secret on the Django server; the frontend receives only short lived room tokens.

### Optional Redis

Redis is included as a local Docker Compose service:

```bash
docker compose up -d redis
```

Then set `CHANNEL_REDIS_URL=redis://127.0.0.1:6379/0` in `.env` and restart. Without Redis, Channels uses its in-memory layer for a single local Daphne process. Production and multiple ASGI workers require Redis so all workers share groups and events.

### Email and password reset

Local development uses Django's console email backend. Invitation and password reset messages appear in `.run/backend.log`. For a deployed service, set `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (or `EMAIL_USE_SSL`), and `DEFAULT_FROM_EMAIL`. Use an app password or transactional mail credentials and keep them out of source control.

### Environment variables

Copy `.env.example` to `.env` if you are configuring services manually. `./start.sh` creates it automatically on first run.

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Private Django signing key; generated by the startup script for local use |
| `DJANGO_DEBUG` | `true` for local development; set `false` in production |
| `DJANGO_ALLOWED_HOSTS` | Comma separated Django hostnames |
| `FRONTEND_ORIGIN` | Comma separated browser origins allowed by CORS and WebSocket origin validation |
| `BACKEND_PORT`, `FRONTEND_PORT` | Optional starting ports used by `./start.sh`; if occupied, the next free port is selected |
| `DATABASE_URL` | `sqlite:///db.sqlite3` locally or a PostgreSQL URL |
| `CHANNEL_REDIS_URL` | Redis URL for shared production Channels groups; blank uses in-memory channels |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | LiveKit server URL and server side credentials |
| `MAX_UPLOAD_MB` | Per file upload limit; defaults to 25 MB |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | Django email backend settings |
| `AWS_STORAGE_BUCKET_NAME` | Enables private S3 compatible storage when configured |
| `AWS_S3_REGION_NAME`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_ADDRESSING_STYLE` | S3 region and optional compatible endpoint configuration |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Standard boto3 credentials for S3 compatible storage |
| `SECURE_HSTS_SECONDS` | Production HSTS duration; defaults to one year |

The frontend reads `frontend/.env`. Copy `frontend/.env.example` to `frontend/.env` to override `VITE_API_URL` or `VITE_WS_URL`.

### S3 compatible storage

Set `AWS_STORAGE_BUCKET_NAME` and the S3 credentials in the backend environment to use private object storage instead of the local `backend/media/` directory. Configure the bucket with private access. ConnectX serves avatars and meeting files through authenticated Django endpoints; it does not expose the shared-file bucket as a public static URL.

## Authentication and security

- API access tokens last 20 minutes; refresh tokens last 7 days, rotate on use, and are blacklisted at logout. The browser refreshes access tokens while the session is active.
- WebSockets validate the JWT passed as a WebSocket subprotocol and enforce allowed browser origins. Meeting consumers check active membership before accepting the connection.
- Meeting passwords are stored as Django password hashes. Password protected meetings require the password even when the joiner already has a membership record, except for the host.
- LiveKit credentials stay server side. A room token is issued only after the caller has joined as an active participant.
- Meeting downloads and avatars require JWT authorization and a permitted meeting relationship. Upload names are stored separately from generated storage paths; file type is checked against both the extension and file contents.
- Production settings enable HTTPS redirection, secure cookies, HSTS, content type sniffing protection, and clickjacking protection. Serve the frontend and API over HTTPS and use `wss://` for Channels and LiveKit.
- DRF applies anonymous and authenticated request throttles; login, registration, and password recovery use a stricter auth throttle.

## API overview

All API routes are under `/api/`. Authenticated routes use `Authorization: Bearer <access-token>`.

| Area | Routes |
|---|---|
| Auth | `POST auth/register/`, `POST auth/login/`, `POST auth/refresh/`, `POST auth/logout/`, `GET/PATCH auth/me/`, `POST auth/change-password/`, `POST auth/forgot-password/`, `POST auth/reset-password/` |
| Meetings | `GET/POST meetings/`, `GET meetings/lookup/{code}/`, `GET/PATCH/DELETE meetings/{uuid}/`, `POST meetings/{uuid-or-code}/join/`, `GET meetings/{uuid}/join/status/`, `POST meetings/{uuid}/leave/`, `POST meetings/{uuid}/end/` |
| Participants | `GET/POST meetings/{uuid}/participants/`, `POST meetings/{uuid}/invite/`, `POST meetings/{uuid}/token/` |
| Chat | `GET/POST meetings/{uuid}/messages/` |
| Files | `GET/POST meetings/{uuid}/files/`, `GET/DELETE files/{id}/` |
| Whiteboard | `GET/POST meetings/{uuid}/whiteboard/` |
| Notifications | `GET notifications/`, `PATCH notifications/{id}/read/`, `PATCH notifications/read-all/` |
| Other | `GET stats/`, `GET avatars/{user_id}/`, `GET health/` |

Participant `POST` actions are `approve`, `mute`, `remove`, and `promote`; requests are checked against the host or co-host role. The `mute` and `remove` actions are also issued to LiveKit when it is configured. Message and whiteboard history are stored in the database; the API returns standard HTTP error codes for missing membership and host-only actions.

### WebSockets

- `ws/meetings/{uuid}/` handles persisted chat, participant presence and status, and synchronized whiteboard snapshots.
- `ws/notifications/` delivers user-specific invitation, reminder, meeting, and message notifications.
- Both endpoints require an access token via the `connectx-auth` WebSocket subprotocol and check the browser Origin. Meeting WebSockets require active meeting membership.

The scheduled-notification process checks every 30 seconds, sends one reminder during the 15 minutes before a scheduled start, and marks due meetings as live. It is started by `./start.sh`. In production run `python manage.py run_notifications` as a separate worker, or schedule `python manage.py run_notifications --once` every minute.

## Testing and builds

```bash
backend/.venv/bin/python backend/manage.py check
backend/.venv/bin/python backend/manage.py makemigrations --check --dry-run
backend/.venv/bin/python backend/manage.py test core -v 2
npm --prefix frontend run build
npm --prefix frontend run lint
```

The Django suite covers registration and email login, meeting creation and external-link validation, privacy-safe invitation lookup, multi-address invitations, protected joins, waiting room approval, host end permissions, membership checks, stored chat and message notifications, PDF upload/list/download/delete, participant lists above 100, whiteboard persistence, reminder deduplication, and an authenticated Channels WebSocket chat exchange. Browser camera/microphone behavior and multi-user media must be checked with browser sessions and a configured LiveKit server.

## Production deployment

### Django API on Render or another ASGI host

Use Python 3.12+, install `backend/requirements.txt`, and set the service working directory to `backend/`. Configure PostgreSQL, Redis, LiveKit, email, and private S3 storage. Set `DJANGO_DEBUG=false`, the production `DJANGO_SECRET_KEY`, backend hostnames in `DJANGO_ALLOWED_HOSTS`, and the frontend origin in `FRONTEND_ORIGIN`.

Example build command:

```bash
pip install -r requirements.txt && python manage.py migrate --noinput && python manage.py collectstatic --noinput
```

Example ASGI start command:

```bash
daphne -b 0.0.0.0 -p $PORT connectx.asgi:application
```

Run `python manage.py run_notifications` as a separate worker. Set the platform health check to `/api/health/`. Use managed Redis and PostgreSQL; do not use the in-memory channel layer or local filesystem for a multi-instance production deployment.

### Frontend on Vercel

Deploy `frontend/` as a Vite project. Add build-time variables:

```dotenv
VITE_API_URL=https://api.example.com/api
VITE_WS_URL=wss://api.example.com
```

Add the Vercel origin to backend `FRONTEND_ORIGIN`. Configure the app fallback so React Router paths such as `/join/{code}` resolve to `index.html`.

### LiveKit connectivity

LiveKit Cloud provides the signaling and TURN services for hosted projects. A self-hosted production SFU needs a public TLS domain, `wss://`, TURN/STUN configuration, UDP and TCP media ports, firewall rules, and a publicly trusted certificate. Follow LiveKit's [deployment](https://docs.livekit.io/transport/self-hosting/deployment/) and [ports/firewall](https://docs.livekit.io/transport/self-hosting/ports-firewall/) guides. Keep its API secret only on the backend.

## Known limits

- Local audio/video uses LiveKit development mode and its public development key pair; use LiveKit Cloud or a secured LiveKit Server for production and multi-device access. Redis is required for shared real-time groups across multiple backend workers.
- The default local SQLite database, media directory, console email, and in-memory Channels layer are for development. Production needs managed PostgreSQL, Redis, SMTP or transactional email, and durable S3 compatible storage.
- ConnectX does not implement end-to-end encryption, recording, meeting transcription, calendar provider sync, or a native mobile app. The responsive web interface supports mobile browsers.
- Device access and WebRTC connectivity depend on browser permissions, HTTPS (except localhost), network/firewall rules, and the selected LiveKit deployment.
