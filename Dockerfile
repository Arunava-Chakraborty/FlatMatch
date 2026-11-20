FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FLATMATCH_DB=/data/flatmatch.db
WORKDIR /srv
RUN pip install --no-cache-dir fastapi==0.136.3 "uvicorn[standard]==0.54.0" pydantic==2.13.4 "psycopg[binary]==3.3.9" psycopg-pool==3.3.3
COPY app ./app
COPY seed.py .
RUN useradd -m appuser && mkdir -p /data && chown appuser /data
USER appuser
EXPOSE 8000
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
