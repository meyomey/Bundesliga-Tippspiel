# Wulmstörper Tipprunde - Docker Image
FROM python:3.11-slim

# System-Abhaengigkeiten
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Arbeitsverzeichnis
WORKDIR /app

# Python-Abhaengigkeiten
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendungscode
COPY . .

# Upload-Verzeichnis erstellen
RUN mkdir -p static/uploads

# Umgebungsvariablen
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

# Port
EXPOSE 5000

# Start-Kommando
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
