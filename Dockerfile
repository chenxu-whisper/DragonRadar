FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache
WORKDIR /app

RUN pip install --no-cache-dir uv==0.8.2
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
RUN uv sync --locked --no-dev && \
    groupadd --gid 10001 dragon && \
    useradd --uid 10001 --gid dragon --no-create-home dragon

ENV PATH="/app/.venv/bin:$PATH"
USER dragon
EXPOSE 8000
ENTRYPOINT ["dragon-radar"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
