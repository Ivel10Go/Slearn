FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000

# entrypoint.sh ist die einzige Quelle für Migration und Serverstart.
# Über "sh" aufgerufen, weil der Build-Kontext die Ausführungsrechte nicht
# garantiert (Checkout unter Windows), und weil Compose es genauso tut.
CMD ["sh", "/app/entrypoint.sh"]
