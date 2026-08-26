FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# LibreOffice headless pour la conversion Office -> PDF (phase 3/4).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Conteneur "outil" : lancé à la demande via `docker compose run --rm pv_ca <cmd>`.
ENTRYPOINT ["python", "-m", "docsia_addons.pv_ca"]
CMD ["--help"]
