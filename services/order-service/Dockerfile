FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/libs

COPY requirements-base.txt ./
COPY services/order-service/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r requirements-base.txt -r service-requirements.txt

COPY libs /app/libs
COPY services/order-service/app /app/app
COPY services/order-service/alembic.ini /app/alembic.ini
COPY services/order-service/alembic /app/alembic
COPY services/order-service/selfcheck.py /app/selfcheck.py
COPY services/order-service/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8003
CMD ["/app/entrypoint.sh"]
