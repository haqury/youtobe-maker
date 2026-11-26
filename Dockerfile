FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-studio.txt /tmp/requirements-studio.txt
RUN pip install --no-cache-dir -r /tmp/requirements-studio.txt

EXPOSE 8501

CMD ["streamlit", "run", "studio/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
