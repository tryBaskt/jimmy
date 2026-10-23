FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY packages/agents/strategy_agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY packages /app/packages
COPY workers /app/workers

RUN useradd --create-home worker
USER worker
CMD ["python", "-m", "workers.strategy_worker.main"]
