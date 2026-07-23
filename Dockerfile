FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    HOST=0.0.0.0 \
    PORT=7070 \
    HARUKA_DIR=/app/data/ \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# bilireq is installed from a pinned Git commit, so git is a build dependency.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Keep dependency installation cacheable when application code changes.
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --prefer-binary \
    --index-url "${PIP_INDEX_URL}" \
    -r /tmp/requirements.txt

# The Playwright image already contains the Chromium version matching
# playwright==1.48.0, so no browser download is needed during the build.
COPY pyproject.toml bot.py ./
COPY .env.example ./.env.prod
COPY haruka_bot ./haruka_bot
RUN mkdir -p /app/data /app/logs

EXPOSE 7070
STOPSIGNAL SIGTERM

CMD ["python", "bot.py"]
