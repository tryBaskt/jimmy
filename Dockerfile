FROM public.ecr.aws/lambda/python:3.12 AS api
WORKDIR ${LAMBDA_TASK_ROOT}
COPY pyproject.toml requirements.lock ./
COPY packages ./packages
COPY apis ./apis
COPY workers ./workers
RUN pip install --no-cache-dir -c requirements.lock .
CMD ["apis.app.handler"]

FROM python:3.12-slim AS worker
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY packages ./packages
COPY apis ./apis
COPY workers ./workers
RUN pip install --no-cache-dir -c requirements.lock . && useradd --uid 10001 --create-home runner
USER 10001
ENTRYPOINT ["python", "-m", "workers.replication.main"]
