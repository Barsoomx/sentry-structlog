FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY . .
RUN uv sync --locked --all-groups

CMD ["uv", "run", "--no-sync", "pytest"]
