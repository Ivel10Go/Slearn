FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ffmpeg ist kein Komfort, sondern eine Abhaengigkeit: die Aufnahme kommt als
# WebM/Opus aus dem Browser, und Piper erzeugt Rohwav. Ohne ffmpeg kann der
# Backend weder umwandeln noch die Aufnahme pruefen, und der Endpunkt antwortet
# dann mit 503 - auch fuer eine WAV-Datei, die ohne den Weg durchginge.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000

# entrypoint.sh ist die einzige Quelle für Migration und Serverstart.
# Über "sh" aufgerufen, weil der Build-Kontext die Ausführungsrechte nicht
# garantiert (Checkout unter Windows), und weil Compose es genauso tut.
CMD ["sh", "/app/entrypoint.sh"]
