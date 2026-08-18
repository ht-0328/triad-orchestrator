FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY triad ./triad
COPY bin ./bin
COPY tests ./tests
COPY config ./config
COPY contracts ./contracts
COPY templates ./templates
COPY docs/knowledge ./docs/knowledge

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
