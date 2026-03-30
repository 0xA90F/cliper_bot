FROM python:3.11-slim

# Install system dependencies: ffmpeg + yt-dlp deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Non-root user for security
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python", "bot.py"]
