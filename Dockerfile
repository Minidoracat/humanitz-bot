FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml .python-version ./
RUN uv sync --no-dev --no-install-project

# Copy source code
COPY src/ src/

# Create runtime directories
RUN mkdir -p data tmp logs

CMD ["uv", "run", "python", "-m", "humanitz_bot"]
