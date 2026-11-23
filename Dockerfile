# One-command local run of the Streamlit app. Multi-arch base image (amd64 + arm64).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Dependencies first: this layer is reused until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY agent/ agent/
COPY .streamlit/ .streamlit/
COPY app.py cli.py ./
COPY data/download_chinook.py data/

# Chinook (1 MB, MIT) ships in the image. Olist is mounted at run time (docker-compose.yml),
# and no .env is ever copied: keys come in as environment variables when the container starts.
RUN python data/download_chinook.py && useradd --create-home app
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
# 0.0.0.0 inside the container only, so Docker's port mapping can reach the app.
# docker-compose.yml still publishes it on the host's 127.0.0.1.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
