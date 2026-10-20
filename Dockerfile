# syntax=docker/dockerfile:1
# Stage 1: compile the C collector and run its unit tests.
FROM debian:bookworm-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libsqlite3-dev make \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY Makefile ./
COPY collector/ collector/
COPY tests/fixtures/ tests/fixtures/
RUN make test-c && make build

# Stage 2: runtime image with the collector, the Python package and the dashboard.
FROM python:3.15-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libsqlite3-0 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 syspulse \
 && mkdir /data && chown syspulse /data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=build /src/build/syspulse-collector /usr/local/bin/syspulse-collector
COPY syspulse/ syspulse/
COPY dashboard/ dashboard/
COPY .streamlit/ .streamlit/
COPY docs/FAQ.md docs/FAQ.md
COPY data/live-run.db data/live-run.db
COPY scripts/ scripts/
USER syspulse
ENV SYSPULSE_DB=/data/metrics.db \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
VOLUME /data
EXPOSE 8501
# Default: the dashboard. The collector runs as a separate container, see docker-compose.yml.
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
