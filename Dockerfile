# Stage 1: get kubectl from official image
FROM bitnami/kubectl:latest AS kubectl

# Stage 2: build the pipeline image
FROM python:3.12-alpine

# Copy kubectl from stage 1
COPY --from=kubectl /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/kubectl

# Runtime deps for shell scripts
RUN apk add --no-cache bash curl

# Install Python dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy pipeline scripts
COPY bdc_utils.py \
     bdc_data_manager.py \
     fetch_jira_data.py \
     fetch_studies.py \
     update_programs.py \
     deploy_mappings.py \
     diff_runs.py \
     run_pipeline.sh \
     ./

RUN chmod +x run_pipeline.sh

# /data — mount PersistentVolume here for run outputs
VOLUME /data

# Environment variables — inject secrets via Kubernetes Secret, never bake in
ENV JIRA_URL=https://nhlbijira.nhlbi.nih.gov \
    DEPLOY_NAMESPACE=bdc-search-dev

ENTRYPOINT ["bash", "run_pipeline.sh"]
CMD ["-o", "/data"]
