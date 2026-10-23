# Development

## Stack

- **Backend:** Python 3, Flask, ffmpeg (subprocess)
- **Frontend:** React 19, Vite, ShadCN UI (Radix + Tailwind CSS), React Router v7, Axios, Recharts
- **Container:** Single Docker image, Flask serves both API and built React static files

## Quick start (dev)

```bash
cp .env.template .env
# Edit .env with your Dispatcharr credentials

docker compose -f docker-compose.dev.yml up
```

- Frontend with hot-reload: http://localhost:3000
- Backend API: http://localhost:5000/api

The Vite dev server proxies `/api/*` to the backend.

## Frontend

Changes to `frontend/src/` hot-reload automatically.

```
frontend/src/
  pages/          # Route-level page components
  components/     # Shared UI components
    ui/           # ShadCN primitives
    layout/       # Sidebar, shell layout
  services/       # apiClient.js (axios wrapper)
  hooks/          # Custom React hooks
  lib/            # Utilities (cn, etc.)
```

Add a ShadCN component:

```bash
cd frontend
npx shadcn@latest add <component-name>
```

## Backend

Python files in `backend/` are volume-mounted. After edits:

```bash
docker compose -f docker-compose.dev.yml restart backend
```

Key files:

| File                           | Role                                                 |
| ------------------------------ | ---------------------------------------------------- |
| `web_api.py`                   | All Flask routes                                     |
| `automated_stream_manager.py`  | M3U update, regex matching, stream assignment        |
| `stream_checker_service.py`    | ffmpeg quality checking, scoring, reordering         |
| `scheduling_service.py`        | Period scheduler, EPG events                         |
| `automation_config_manager.py` | Profile/period/assignment CRUD                       |
| `stream_monitoring_service.py` | Live monitoring session management                   |
| `udi/`                         | Universal Data Index — cached Dispatcharr data layer |

## Logs

```bash
docker compose -f docker-compose.dev.yml logs -f            # all
docker compose -f docker-compose.dev.yml logs -f backend    # backend only
docker compose -f docker-compose.dev.yml logs -f frontend   # frontend only
```

## Building for production

```bash
cd frontend && npm run build
cd ..
docker build -t streamflow:local .
docker compose up
```

## Tests

```bash
cd backend
python -m pytest tests/
```

Tests are organized by module under `backend/tests/`.

## Tips

- Set `DEBUG_MODE=true` in `.env` for verbose backend logging
- API requests from the frontend always go through `/api/` — backend never called directly from the browser in production (single port)
- All persistent config is in the Docker volume at `/app/data` — wipe it with `docker compose down -v` for a clean slate
- The UDI caches Dispatcharr data in memory and on disk; if data looks stale, trigger a UDI refresh from the UI or call `POST /api/udi/refresh`
