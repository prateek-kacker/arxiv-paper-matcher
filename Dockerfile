FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAPER_MATCHER_DB_PATH=/tmp/paper_matcher.db

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core_engine.py server.py batch_runner.py Archive_research_multi-agent_debate.py ./
COPY static ./static

EXPOSE 8080

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]