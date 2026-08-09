FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/app/libs
COPY requirements-base.txt ./
COPY services/market-data-service/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r service-requirements.txt
COPY libs /app/libs
COPY services/market-data-service/app /app/app
COPY services/market-data-service/alembic.ini /app/alembic.ini
COPY services/market-data-service/alembic /app/alembic
COPY services/market-data-service/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
EXPOSE 8005
CMD ["/app/entrypoint.sh"]
