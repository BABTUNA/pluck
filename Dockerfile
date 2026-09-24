FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && uv pip install --system -r pyproject.toml
COPY . .
# pre-warm the embedding model and the 5595 category vectors at build time so
# a worker never stalls downloading/computing them on a tiny shared cpu
RUN python -c "from pluck.taxonomy import snap; snap('Health & Beauty > Fragrances')"
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
