FROM node:22-bookworm-slim AS codex
ARG CODEX_PACKAGE=@openai/codex@latest
RUN npm install --global "$CODEX_PACKAGE" && npm cache clean --force

FROM python:3.12-slim-bookworm
ARG INSTALL_BABELDOC=0
ARG BABELDOC_PACKAGE=BabelDOC
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAPERSTUDIO_DATA_DIR=/data \
    PAPERSTUDIO_HOST=0.0.0.0 \
    PAPERSTUDIO_SEED_DEMO=0
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
RUN if [ "$INSTALL_BABELDOC" = "1" ]; then python -m pip install --no-cache-dir "$BABELDOC_PACKAGE"; fi
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/lib/node_modules/@openai/ /opt/codex-cli/@openai/
RUN ln -s /opt/codex-cli/@openai/codex/bin/codex.js /usr/local/bin/codex
COPY . ./
USER paperstudio
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "run.py", "--no-demo"]
