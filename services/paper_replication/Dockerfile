FROM python:3.12-slim AS builder
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY packages/ /shared/
COPY services/paper_replication/ ./
RUN pip install --no-cache-dir /shared ".[runtime]"
FROM python:3.12-slim
COPY --from=builder /opt/venv /opt/venv
RUN useradd --create-home --uid 10001 jimmy
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 JIMMY_WORKSPACE=/tmp/jimmy
USER jimmy
EXPOSE 8080
CMD ["python", "-m", "runtime"]
