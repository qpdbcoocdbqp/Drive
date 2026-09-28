FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    GRADIO_ANALYTICS_ENABLED=False \
    CODEX_HOME=/var/lib/codex

WORKDIR /app

COPY src/oai_skill/deployment/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

COPY src/oai_skill /app/src/oai_skill

RUN mkdir -p /var/lib/codex /workspace /skills \
    && chown -R app:app /app /var/lib/codex /workspace /skills

USER app

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "oai_skill.server.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
