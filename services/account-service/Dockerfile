FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/libs

COPY requirements-base.txt ./
COPY services/account-service/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r requirements-base.txt -r service-requirements.txt

COPY libs /app/libs
COPY services/account-service/app /app/app
COPY services/account-service/alembic.ini /app/alembic.ini
COPY services/account-service/alembic /app/alembic
COPY services/account-service/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8002
CMD ["/app/entrypoint.sh"]
