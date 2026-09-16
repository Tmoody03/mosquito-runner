FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py strategy.py ./
COPY templates ./templates
COPY static ./static
RUN useradd --create-home --uid 10001 mosquito && mkdir -p /data && chown -R mosquito:mosquito /app /data
ENV PORT=8080
ENV MOSQUITO_STATE_FILE=/data/mosquito-runner-state.json
EXPOSE 8080
# Railway mounts volumes after the image is built, so the build-time /data
# ownership can be replaced by root ownership. Repair it at container start,
# then drop privileges before starting the web process.
CMD ["sh", "-c", "chown -R mosquito:mosquito /data && exec runuser -u mosquito -- gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 180"]
