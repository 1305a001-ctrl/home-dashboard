FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml /app/
COPY app /app/app

RUN pip install --upgrade pip && pip install -e .

# Audit log dir (mounted as volume in prod)
RUN mkdir -p /var/log/home-dashboard

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8090/api/health', timeout=3); exit(0 if r.status_code == 200 else 1)"

CMD ["python", "-m", "app.main"]
