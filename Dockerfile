FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY presets.example.json ./
ENTRYPOINT ["xiaomi-health-sync"]
CMD ["show-config"]
