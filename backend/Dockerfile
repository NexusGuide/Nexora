# =============================================================================
# Nexus VPN backend image
#
# No secret is baked in: there is no ARG or ENV carrying a credential, and .env
# is excluded by .dockerignore. Configuration arrives at run time through the
# environment, so the same image is safe to push to a public registry.
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# --- dependencies (cached independently of source changes) -------------------
FROM base AS deps
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libpq5 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /usr/sbin/nologin --uid 10001 nexus

WORKDIR /app
COPY --from=deps /install /usr/local

# Copy the whole tree rather than an allowlist of paths: a COPY allowlist
# silently drops a newly added module and the container crash-loops in
# production. .dockerignore is what keeps secrets and junk out.
COPY --chown=nexus:nexus . /app

USER nexus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
