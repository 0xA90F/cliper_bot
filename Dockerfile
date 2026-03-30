FROM python:3.11-slim

# Install system deps: ffmpeg with libass support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libass9 \
    libass-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects env vars — no .env file needed in production
CMD ["python", "bot.py"]
