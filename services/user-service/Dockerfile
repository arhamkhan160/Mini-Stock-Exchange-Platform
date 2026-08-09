FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/libs

COPY requirements-base.txt ./
COPY services/user-service/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r requirements-base.txt -r service-requirements.txt

COPY libs /app/libs
COPY services/user-service/app /app/app
COPY services/user-service/alembic.ini /app/alembic.ini
COPY services/user-service/alembic /app/alembic
COPY services/user-service/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8001
CMD ["/app/entrypoint.sh"]
