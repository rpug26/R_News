FROM python:3.11-slim

WORKDIR /app

# curl_cffi / networking
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default state dir (override with Railway volume + STATE_DIR)
ENV STATE_DIR=/data
ENV SCAN_INTERVAL_MINUTES=15
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /data

CMD ["python", "worker.py"]
