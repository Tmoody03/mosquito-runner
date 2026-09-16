FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py strategy.py simulator.py protection.py engine.py lifecycle.py scheduler.py ./
COPY templates ./templates
COPY static ./static
RUN useradd --create-home --uid 10001 mosquito && mkdir -p /data && chown -R mosquito:mosquito /app /data
ENV PORT=8080 \
    MOSQUITO_STATE_FILE=/data/mosquito-runner-state.json \
    MOSQUITO_SIM_FILE=/data/mosquito-simulation.json \
    MOSQUITO_ENGINE_FILE=/data/mosquito-engine.json \
    MOSQUITO_LIFECYCLE_FILE=/data/mosquito-lifecycle.json \
    MOSQUITO_SCHEDULER_FILE=/data/mosquito-scheduler.json \
    MOSQUITO_RUNTIME_ENABLED=1 \
    MOSQUITO_REBUY_COOLDOWN_MINUTES=5 \
    ALPACA_LIVE_TRADING=0
EXPOSE 8080
# Railway mounts volumes after the image is built, so the build-time /data
# ownership can be replaced by root ownership. Repair it at container start,
# then drop privileges before starting the web process.
CMD ["sh", "-c", "chown -R mosquito:mosquito /data && exec runuser -u mosquito -- gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 180"]
