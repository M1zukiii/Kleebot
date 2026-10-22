FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY tests ./tests
COPY scripts ./scripts
COPY assets ./assets
COPY help.txt .
COPY klee_prompt.txt .

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 CMD python -c "import time; from pathlib import Path; p=Path('/tmp/kleebot-ready'); assert p.exists() and time.time()-p.stat().st_mtime < 45"

CMD ["python", "-m", "bot.main"]
