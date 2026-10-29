FROM node:22-bookworm-slim AS codex-cli
ARG CODEX_PACKAGE=@openai/codex@0.165.0
RUN npm install --global "$CODEX_PACKAGE" && npm cache clean --force

FROM python:3.12-slim-bookworm AS core
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAPERSTUDIO_DATA_DIR=/data \
    PAPERSTUDIO_HOST=0.0.0.0 \
    PAPERSTUDIO_SEED_DEMO=0
ARG VERSION=0.5.0
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/Linchunhui/PaperStudio" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION"
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libstdc++6 libgomp1 libglib2.0-0 libgl1 fontconfig fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 paperstudio \
    && useradd --uid 10001 --gid paperstudio --create-home paperstudio \
    && mkdir -p /data /home/paperstudio/.codex /home/paperstudio/.cache \
    && chown -R paperstudio:paperstudio /data /home/paperstudio
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
COPY skills ./skills
COPY demo ./demo
COPY scripts ./scripts
COPY config/*.json ./config/
COPY run.py LICENSE THIRD_PARTY_NOTICES.md DATA_NOTICE.md VERSION ./
USER paperstudio
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "run.py", "--no-demo"]

# Full original dataset; no existing shards are removed or sampled.
FROM core AS full
COPY config/catalog ./config/catalog
COPY config/relations ./config/relations
COPY config/reading-cards ./config/reading-cards

# Legacy source builds retain the optional CLI and full bundled data.
FROM full AS codex
COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/lib/node_modules/@openai/ /opt/codex-cli/@openai/
USER root
RUN ln -s /opt/codex-cli/@openai/codex/bin/codex.js /usr/local/bin/codex
ARG INSTALL_BABELDOC=0
ARG BABELDOC_PACKAGE=BabelDOC==0.6.4
# Isolate BabelDOC's dependency set from the web service.
RUN if [ "$INSTALL_BABELDOC" = "1" ]; then \
      python -m venv /opt/babeldoc && /opt/babeldoc/bin/pip install --no-cache-dir "$BABELDOC_PACKAGE" \
      && ln -s /opt/babeldoc/bin/babeldoc /usr/local/bin/babeldoc; fi
USER paperstudio
