# backend/Dockerfile
#
# Build context is the REPO ROOT, not backend/ — e.g. `docker build -f backend/Dockerfile .`
# (docker-compose.yml sets this via `build: {context: ., dockerfile: backend/Dockerfile}`).
# This is required, not a convenience: several backend modules import from a sibling
# `mechanism/` package at runtime via an explicit sys.path append (see
# backend/services/bot_access_service.py, momentum_board_service.py,
# telegram_control_service.py), and backend/utils.py's find_project_root() walks up from
# the working directory looking for `mechanism/` and `breakout_results/` as siblings of
# `backend/` (CLAUDE.md's documented `cd backend && uvicorn main:app` layout). Building
# from backend/ alone would make both of those fail at runtime.
FROM python:3.11-slim

# libgomp1: xgboost's shared library needs OpenMP at runtime (routers/ml_stats.py loads
# a trained model directly via joblib to read feature importances).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
COPY mechanism/requirements.txt mechanism/requirements.txt
# Also installing mechanism/requirements.txt: backend/services/telegram_control_service.py and
# bot_access_service.py import mechanism.alerts modules by path (see the sys.path comment above),
# and that import chain transitively needs packages (asyncpg at minimum) that aren't in
# backend/requirements.txt on its own — found empirically during Phase 2 local validation
# (both routers logged "not available" at startup until this was added; main.py's own
# try/except-per-router design meant the app still started, just with those two degraded).
RUN pip install --no-cache-dir -r backend/requirements.txt -r mechanism/requirements.txt

COPY backend/ backend/
COPY mechanism/ mechanism/
# bot_access_service.py's import chain reaches into ml_training/ as well (mirrors the
# mechanism/ml_enhancement <-> ml_training dependency documented in CLAUDE.md's data-flow section)
# — same empirical finding as above.
COPY ml_training/ ml_training/

# find_project_root() (backend/utils.py) requires `breakout_results/` to exist as a
# sibling of `mechanism/`; `frontend_data/` is read by main.py's dashboard fetch. Both
# are populated by the mechanism pipeline at runtime via a mounted volume — create them
# here so a freshly-built image has somewhere to look before the first pipeline run.
RUN mkdir -p breakout_results frontend_data

# Deliberately NOT copying a .env file into the image (see .dockerignore). Config is
# injected as real container environment variables by Compose (`env_file:` +
# `environment:`). backend/main.py calls load_dotenv(path, override=True) against
# /app/.env — with no such file present in the image, that call is a silent no-op and
# the Compose-injected environment variables are what the app actually sees.
WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=3).status == 200 else 1)"

# No --reload: that's a development flag (file-watcher, single worker, reloader-parent
# process) already linked in CLAUDE.md to a reproducible shutdown hang — see
# docs/devops/CURRENT_ARCHITECTURE.md §7.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
