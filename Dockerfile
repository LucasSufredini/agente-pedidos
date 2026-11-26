FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    PORT=7860

# Usuário sem root com uid 1000, exigido pelo Hugging Face Spaces.
RUN useradd --create-home --uid 1000 app
WORKDIR /home/app/agente

# Dependências antes do código: mudar o código não reinstala tudo no próximo build.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app app/ app/
COPY --chown=app:app data/ data/

USER app
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/health', timeout=4)"

CMD ["sh", "-c", "uvicorn app.servidor:app --host 0.0.0.0 --port ${PORT}"]
