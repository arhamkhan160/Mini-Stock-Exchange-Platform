FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/libs

COPY requirements-base.txt ./
COPY services/gateway/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r requirements-base.txt -r service-requirements.txt

COPY libs /app/libs
COPY services/gateway/app /app/app

# No database, so no migrations and no entrypoint script.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
