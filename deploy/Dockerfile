# Сборка чек-бота для Fly.io (или любого Docker-хоста).
FROM python:3.14-slim

# Tesseract с русским языком + зависимости сборки opencv-headless не нужны (колёса есть)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng tesseract-ocr-osd \
        bash coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src /app/src
COPY config /app/config

# Языковые модели из системы; путь без кириллицы — код это и любит
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
ENV PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1

COPY deploy/start.sh /app/deploy/start.sh
RUN chmod +x /app/deploy/start.sh

CMD ["/bin/bash", "/app/deploy/start.sh"]