FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 botuser \
    && mkdir -p /data \
    && chown botuser:botuser /data
COPY --chown=botuser:botuser bot.py task_queue.py todus_s3_storage.py platform_downloads.py telegram_backup.py toduss3_welcome_option_1.png .
USER botuser
CMD ["python", "bot.py"]
