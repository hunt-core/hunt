ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app

# Build essentials + client libraries for the PostgreSQL and MySQL drivers
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git curl build-essential libpq-dev default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching host UID 1000
RUN groupadd --force -g 1000 hunt \
    && useradd -ms /bin/bash --no-user-group -g 1000 -u 1000 hunt

# Layer-cached dependency install. The build backend needs the README and the
# package source, so copy them before installing. compose.yaml bind-mounts the
# working tree over /app at run time, which keeps the install editable.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint
RUN chmod +x /usr/local/bin/docker-entrypoint \
    && chown -R hunt:1000 /app

USER hunt

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint"]
CMD ["hunt", "serve", "--host", "0.0.0.0", "--port", "8000", "--reload"]
