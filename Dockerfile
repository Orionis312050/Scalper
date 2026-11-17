FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr fonts-dejavu-core \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[ocr,ocr-paddle]"

RUN useradd -m scalper && mkdir -p /app/data /app/out && chown -R scalper:scalper /app

# Le conteneur démarre en root : l'entrypoint chown les répertoires montés
# (bind mounts root côté hôte, ex. Coolify) puis droppe les privilèges (setpriv).
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["scalper", "serve", "--host", "0.0.0.0", "--port", "8000"]
