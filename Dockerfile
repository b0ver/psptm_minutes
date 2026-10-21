FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN useradd --uid 10001 --create-home minutes && mkdir /data && chown minutes:minutes /data
FROM dependencies AS runtime
COPY app ./app
COPY config ./config
USER minutes
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM runtime AS test
USER root
COPY requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY tests ./tests
COPY scripts/ops ./scripts/ops
COPY integrations/telemost ./integrations/telemost
USER minutes
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM dependencies AS gpu
USER root
COPY requirements-gpu.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-gpu.txt
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib \
    NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility
COPY app ./app
COPY config ./config
USER minutes
CMD ["python", "-m", "app.worker"]
