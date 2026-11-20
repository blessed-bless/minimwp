FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder /install /usr/local
COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser

ENV DATABASE_URL=sqlite+aiosqlite:///./data/crm.db
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
