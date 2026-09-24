FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && uv pip install --system -r pyproject.toml
COPY . .
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
