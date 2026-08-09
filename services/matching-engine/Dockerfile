FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/libs

COPY requirements-base.txt ./
COPY services/matching-engine/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r requirements-base.txt -r service-requirements.txt

COPY libs /app/libs
COPY services/matching-engine/app /app/app
COPY services/matching-engine/selfcheck.py /app/selfcheck.py
# No alembic: the matching engine has no database. The book is in memory.
COPY services/matching-engine/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8004
CMD ["/app/entrypoint.sh"]
