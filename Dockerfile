# ---- stage 1: build the React UI ----
FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm install --no-fund --no-audit
COPY ui/ .
RUN npm run build
# vite outDir is ../api/static -> lands in /api/static

# ---- stage 2: the service ----
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY engine/ engine/
COPY mining/ mining/
COPY modelling/ modelling/
COPY api/ api/
COPY extraction/ extraction/
COPY fixtures/ fixtures/
COPY --from=ui /api/static api/static

# Persist workspaces outside the container: mount a volume at /data
ENV PROCESSIQ_DATA=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_PORT=8000
ENV AWS_LWA_READINESS_CHECK_PATH=/
ENV AWS_LWA_INVOKE_MODE=RESPONSE_STREAM
