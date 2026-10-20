FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl wget git unzip procps build-essential ca-certificates gnupg ffmpeg fontconfig fonts-noto-core fonts-noto-color-emoji fonts-hosny-amiri fonts-dejavu-core libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libxshmfence1 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y --no-install-recommends nodejs && fc-cache -fv && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir flask requests faster-whisper==1.1.1 gunicorn
COPY . .
EXPOSE 7860
CMD ["python", "main.py"]