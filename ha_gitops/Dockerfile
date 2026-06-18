ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime tools: git for repo ops, openssh-client reserved for a future
# SSH-key auth path.
RUN apk add --no-cache git openssh-client

WORKDIR /app

# Install Python deps. build deps are added in a virtual package so they can be
# dropped from the final image (kept as a safety net; the pinned deps ship
# musllinux wheels for cp313 on both aarch64 and amd64, so nothing is compiled).
COPY requirements.txt ./
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apk del .build-deps

COPY engine ./engine
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.version="0.1.2" io.hass.type="addon"

CMD ["/run.sh"]
