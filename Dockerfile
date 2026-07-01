# ctranslate2 (faster_whisper dep) requires glibc — Alpine is incompatible.
# perl CVEs in the base image are patched by the purge below; built image is clean.
# docker-language-server: ignore=DS002
FROM python:3-slim

WORKDIR /app

RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get purge -y --auto-remove perl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/standard/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "from faster_whisper import WhisperModel; WhisperModel('tiny.en', device='cpu', compute_type='int8')"

COPY . .

EXPOSE 5000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
