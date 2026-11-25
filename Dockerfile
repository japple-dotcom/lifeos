FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lifeos ./lifeos
COPY config ./config
COPY scripts ./scripts
COPY web ./web
COPY lifeos_boot.py .env.example ./
ENV TZ=Europe/London LIFEOS_HOME=/app LIFEOS_NO_TRAY=1
EXPOSE 8800
CMD ["python", "lifeos_boot.py", "--headless"]
