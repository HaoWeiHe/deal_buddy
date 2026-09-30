# DealBuddy worker: the same code as the Vercel app, running the always-on loop in dealbuddy/worker.py.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dealbuddy ./dealbuddy
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "dealbuddy.worker"]
