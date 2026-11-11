FROM python:3.15-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
RUN useradd -r -u 10001 cf && chown -R cf /srv
# media library (STORAGE_BACKEND=local): the compose services run as uid 997, so the named volume must start out writable by it
RUN mkdir -p /data/assets && chown 997:997 /data/assets && chmod 775 /data/assets
USER cf
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
