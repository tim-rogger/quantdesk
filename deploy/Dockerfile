# Ein Image für Bot (Scheduler) und Dashboard. Build-Kontext ist das Repo (siehe docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY quantdesk/ quantdesk/
COPY dashboard/ dashboard/
COPY data/universe.csv data/universe.csv
COPY run_daily.py scheduler.py forward_test.py research.py backtest.py ./

# nicht als root laufen; Laufzeitdaten liegen im Volume /state
RUN useradd --create-home --uid 1000 bot && mkdir -p /state && chown bot:bot /state
USER bot

CMD ["python", "scheduler.py"]
