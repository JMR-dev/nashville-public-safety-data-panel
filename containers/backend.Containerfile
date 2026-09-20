# The API and the worker are the same application run with different subcommands, so they are
# the same image: one build, one set of dependencies, one thing to update.
FROM docker.io/library/python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/srv/panel/.venv

WORKDIR /src
# Dependencies first: they change far less often than the application does.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable
COPY backend/panel ./backend/panel
RUN uv sync --frozen --no-dev --no-editable


FROM docker.io/library/python:3.14-slim

# A fixed id so the data volume's ownership is the same wherever the image runs.
RUN useradd --uid 10001 --user-group --home-dir /var/lib/panel --create-home --shell /usr/sbin/nologin panel

COPY --from=build /srv/panel/.venv /srv/panel/.venv

ENV PATH=/srv/panel/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PANEL_DATABASE=/var/lib/panel/panel.sqlite \
    PANEL_BACKUP_DIR=/var/lib/panel/backups

USER panel
WORKDIR /var/lib/panel
VOLUME /var/lib/panel

# The worker owns the database and the API only reads, so either may be started first.
ENTRYPOINT ["panel"]
CMD ["api", "--host", "0.0.0.0", "--port", "8001"]
