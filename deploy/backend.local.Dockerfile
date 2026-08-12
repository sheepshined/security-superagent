# Local-only Dockerfile for environments where Docker Hub is unreachable.
# Bases on the previously built deploy-backend image (which already contains
# python:3.11-slim and the base deps), then overlays the current backend code
# and installs the one new dependency. NOT for CI/release use.
FROM deploy-backend:latest

WORKDIR /app

COPY backend ./backend
RUN pip install --default-timeout=300 --retries 10 "pycryptodome>=3.20,<4"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
