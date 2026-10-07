FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
WORKDIR /app
COPY pyproject.toml constraints-service.txt ./
COPY src ./src
RUN pip install --no-cache-dir -c constraints-service.txt ".[service]" \
    && useradd --uid 10001 --create-home kg \
    && mkdir /jobs && chown kg:kg /jobs
USER 10001:10001
ENV KG_JOB_ROOT=/jobs
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"
CMD ["uvicorn", "adaptive_kg_reasoning.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
