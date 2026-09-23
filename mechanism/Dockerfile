# mechanism/Dockerfile
#
# One shared image for three roles that already share almost all of mechanism/requirements.txt:
#   - the Telegram bot (long-running: `python mechanism/alerts/run_bot.py`)
#   - the nightly pipeline (one-shot: `./automation_pipeline.sh`, via `docker compose run --rm pipeline`)
#   - the channel senders (one-shot: e.g. `python mechanism/alerts/send_daily_digest.py ...`)
# See docs/devops/CICD_STRATEGY.md §2 and RESOURCE_REQUIREMENTS.md §5 for why this is one image, not
# three near-duplicates. Build context is the REPO ROOT (needs mechanism/, ml_training/, and
# automation_pipeline.sh together — same reasoning as backend/Dockerfile).
FROM python:3.11-slim

# bash: automation_pipeline.sh is a bash script (set -o pipefail, [[ ]], BASH_SOURCE) — not guaranteed
#   present on every Debian slim variant, installed explicitly rather than assumed.
# libgomp1: xgboost (ml_enhancement's inference path, and ml_training's own trainer) needs OpenMP.
# ca-certificates: outbound HTTPS to Tiingo/Alpaca/yfinance/Telegram/Alpaca-news.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY mechanism/requirements.txt mechanism/requirements.txt
COPY ml_training/requirements.txt ml_training/requirements.txt
RUN pip install --no-cache-dir -r mechanism/requirements.txt -r ml_training/requirements.txt

COPY mechanism/ mechanism/
COPY ml_training/ ml_training/
COPY automation_pipeline.sh automation_pipeline.sh
RUN chmod +x automation_pipeline.sh

# automation_pipeline.sh resolves its interpreter as
# "$SCRIPT_DIR/.venv/Scripts/python.exe" (a Windows venv layout — this project's own local
# dev convention, see CLAUDE.md §8) and refuses to run if that exact path doesn't exist.
# Rather than editing the script (kept byte-for-byte identical to what runs outside Docker,
# so it can't silently drift from what's actually tested there), satisfy the check with a
# symlink to this image's real interpreter. ".exe" is just a filename on Linux — no .exe
# semantics implied — and `python` here already resolves to the one interpreter this whole
# image installed everything into.
RUN mkdir -p .venv/Scripts && ln -s "$(command -v python)" .venv/Scripts/python.exe

# Output/working directories the scripts write to via relative paths (see automation_pipeline.sh's
# `LOG_DIR="logs"` and the various updaters/senders) — populated at runtime via mounted volumes;
# created here so a fresh container has somewhere to write before the first real run.
RUN mkdir -p logs data breakout_results frontend_data reports

# matplotlib (mechanism/alerts/chart.py, channel_cards.py) wants a writable font-cache dir on
# first import; avoid it defaulting to a home directory that may not be writable.
ENV MPLCONFIGDIR=/tmp/matplotlib

# Deliberately NOT copying a .env file into the image — see backend/Dockerfile's comment and
# .dockerignore; mechanism/shared/config.py's load_env_file() gracefully no-ops if none of its
# candidate paths exist on disk and falls back to os.getenv(), i.e. Compose's injected env vars.

# No fixed CMD: the `bot` service overrides command to run_bot.py (long-running); `pipeline` and
# `channel-sender` are invoked via `docker compose run --rm <service> <command>`, never `up -d`.
CMD ["python", "--version"]
