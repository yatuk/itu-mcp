# Base image pinned by digest (python:3.12-slim, multi-arch index, read with
# `docker buildx imagetools inspect python:3.12-slim`). Update the tag and the
# digest together.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

# The server listens on every interface inside the container, so it refuses
# to start unless these are passed at run time:
#   NINOVA_REMOTE_API_KEY                            a long random key
#   NINOVA_ALLOWED_HOSTS or NINOVA_PUBLIC_BASE_URL   the public host name
# No key and no opt-out is set here on purpose. See docs/advanced.md.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    NINOVA_REMOTE_HOST=0.0.0.0 \
    NINOVA_STATE_DIR=/data

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir . && \
    groupadd --system --gid 10001 itumcp && \
    useradd --system --uid 10001 --gid itumcp --home-dir /data --no-create-home \
        --shell /usr/sbin/nologin itumcp && \
    mkdir -p /data && \
    chown itumcp:itumcp /data && \
    chmod 700 /data

USER itumcp:itumcp

# Session cookies, snapshots and tracking state. Mount a volume to keep them.
VOLUME ["/data"]

EXPOSE 8000

CMD ["itu-mcp-remote"]
