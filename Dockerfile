FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAPER_MATCHER_DB_PATH=/tmp/paper_matcher.db

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY Archive_research_multi-agent_debate.py ./

EXPOSE 8080

CMD ["sh", "-c", "streamlit run Archive_research_multi-agent_debate.py --server.address 0.0.0.0 --server.port ${PORT:-8080} --server.headless true --browser.gatherUsageStats false --server.enableCORS false --server.enableXsrfProtection false"]