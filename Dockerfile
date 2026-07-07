FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --gid 1000 unprivileged && \
    adduser --uid 1000 --gid 1000 --disabled-password --gecos "" unprivileged && \
    chown -R unprivileged:unprivileged /app

USER unprivileged:unprivileged

COPY pyproject.toml uv.lock* ./
RUN uv sync \
    --no-install-project \
#    --no-group dev \
    --frozen

ENV PATH="/app/.venv/bin:$PATH"

COPY . .

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sleep", "infinity"]
