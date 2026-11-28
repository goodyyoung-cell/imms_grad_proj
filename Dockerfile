FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY sdr_agent ./sdr_agent
RUN pip install --no-cache-dir .

# Cloud Run이 PORT 환경변수를 넣어 준다
CMD ["sh", "-c", "uvicorn sdr_agent.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
