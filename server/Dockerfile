# MeshManager — publieke statistieken- en beheersite voor MeshCore-repeaters
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MM_DATA_DIR=/data

WORKDIR /app

# net-snmp voor de serverzijde SNMP-discovery (snmpget/snmpbulkwalk). Een klein
# OS-pakket, bewust geen Python-SNMP-dependency -- zie app/snmp.py voor de
# afweging. Ontbreekt het toch, dan meldt de discovery-pagina dat netjes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends snmp \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Welke commit dit image is, voor de footer en /api/v1/ping (app/version.py).
# Meegegeven door deploy/autoupdate.sh; leeg bij een build met de hand, dan
# toont de site "dev" in plaats van iets te verzinnen. Staat NA de pip-laag,
# anders zou elke nieuwe commit de dependency-cache ongeldig maken.
ARG MM_BUILD_SHA=""
ARG MM_BUILD_DATE=""
ENV MM_BUILD_SHA=$MM_BUILD_SHA \
    MM_BUILD_DATE=$MM_BUILD_DATE

COPY app/ ./app/

# Data (SQLite + sleutel) staat in een volume, zodat een update van het image
# nooit statistieken of instellingen weggooit.
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
