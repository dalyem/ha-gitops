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

# Install Python deps. build deps are added in a virtual package so they can
# be dropped from the final image. NOTE: pydantic-core (pulled by FastAPI)
# ships musllinux wheels for aarch64/amd64/armv7/i386; armhf (armv6) may need
# a Rust toolchain to build from source — see DOCS.md "Known limitations".
COPY requirements.txt ./
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apk del .build-deps

COPY engine ./engine
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.version="0.1.0" io.hass.type="addon"

CMD ["/run.sh"]
