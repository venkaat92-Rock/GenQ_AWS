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
COPY diagramiq/ diagramiq/
COPY connectors/ connectors/
COPY fixtures/ fixtures/
COPY --from=ui /api/static api/static

# ---- Lambda Web Adapter ----
# Lets the SAME image also run on Lambda: the extension translates Function URL
# invocations into plain HTTP against localhost:8000, so api/main.py is untouched.
# Inert on ECS (nothing invokes the extension there), which is the primary target.
# NOTE: if you do deploy this image to Lambda, the Function URL's invoke mode must
# be set to RESPONSE_STREAM to match AWS_LWA_INVOKE_MODE below, or every response
# comes back empty with a 200.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_PORT=8000
ENV AWS_LWA_READINESS_CHECK_PATH=/
ENV AWS_LWA_INVOKE_MODE=RESPONSE_STREAM

# Workspaces live outside the container at /data. On ECS Fargate that path is an
# EFS access point mounted by the task definition; locally it's a bind mount.
ENV PROCESSIQ_DATA=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
