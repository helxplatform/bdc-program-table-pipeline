# BDC Program Table Pipeline

Automated pipeline that fetches BioData Catalyst (BDC) study data from Jira, the BDC portal, and Gen3, merges program name mappings, and deploys the result to the `search-api` ConfigMap on the sterling Kubernetes cluster.

Runs as a monthly CronJob (1st of every month) and can be triggered manually at any time.

---

## Pipeline Steps

| Step | Script | Description |
|------|--------|-------------|
| 0 | `fetch_jira_data.py` | Fetch program mappings from Jira API (BDC-DMC Joint Workspace) |
| 1 | `fetch_studies.py` | Fetch studies from BDC portal and Gen3 |
| 2 | `update_programs.py` | Merge Jira mappings into program table |
| 3 | *(CronJob)* | Patch `search-api-program-study-mappings` ConfigMap + rollout restart `search-api` |

---

## Running Locally

### Prerequisites

```bash
pip install -r requirements.txt
export JIRA_TOKEN=<your-jira-pat>
```

### Run all steps (skip deploy)

```bash
bash run_pipeline.sh -o /path/to/output --skip-deploy
```

### Run full pipeline including deploy

```bash
bash run_pipeline.sh -o /path/to/output
```

### Key flags

| Flag | Description |
|------|-------------|
| `-o <dir>` | Base output directory (required) |
| `--skip-deploy` | Run steps 0–2 only, skip ConfigMap update |
| `--jira-token <tok>` | Override `$JIRA_TOKEN` env var |

---

## Deploying the Updated Mappings Manually

Use `deploy_mappings.py` to diff and apply a new YAML to `bdc-dev-mappings.yaml`:

```bash
python deploy_mappings.py \
  --input output/Aug_28_2026/program_table/program_table_updated_*.yaml \
  --target /path/to/bdc-dev-mappings.yaml
```

Shows a summary of added/removed/changed entries and prompts for confirmation before writing.

---

## Docker Image

Built natively on the sterling cluster via [kaniko](https://github.com/GoogleContainerTools/kaniko) and pushed to:

```
containers.renci.org/helxplatform/bdc-program-table-pipeline:latest
```

### Rebuild the image

Apply the kaniko build job from within the cluster (requires push credentials secret):

```bash
kubectl apply -f k8s/build-job.yaml -n ykale   # see kaniko setup notes
```

---

## Kubernetes Deployment

All manifests are in `k8s/`.

### Apply manifests

```bash
# 1. Persistent volume (2Gi for pipeline output)
kubectl apply -f k8s/pvc.yaml -n ykale

# 2. Jira token secret (fill in your PAT first)
kubectl create secret generic program-table-secrets \
  --from-literal=JIRA_TOKEN=<your-jira-pat> \
  -n ykale

# 3. RBAC (ServiceAccount + cross-namespace Role/RoleBinding)
kubectl apply -f k8s/rbac.yaml

# 4. CronJob
kubectl apply -f k8s/cronjob.yaml -n ykale
```

### Trigger a manual run

```bash
kubectl create job --from=cronjob/program-table-pipeline manual-$(date +%Y%m%d) -n ykale
```

### Watch logs

```bash
kubectl logs -n ykale -l job-name=manual-<date> -f
```

---

## Configuration

All secrets are injected via Kubernetes Secrets — never baked into the image.

| Environment Variable | Source | Description |
|----------------------|--------|-------------|
| `JIRA_TOKEN` | K8s Secret `program-table-secrets` | Jira Personal Access Token |
| `JIRA_URL` | CronJob env | `https://nhlbijira.nhlbi.nih.gov` |
| `BDC_URL` | CronJob env | `https://search.biodatacatalyst.renci.org/search-api` |
| `GEN3_URL` | CronJob env | `https://gen3.biodatacatalyst.nhlbi.nih.gov/` |
| `DEPLOY_NAMESPACE` | CronJob env | `bdc-search-dev` |

---

## RBAC

The CronJob pod runs as `program-table-sa` (ServiceAccount in `ykale` namespace) with a cross-namespace Role in `bdc-search-dev` granting:

- `configmaps` (`search-api-program-study-mappings`): get, create, update, patch
- `deployments` (`search-api`): get, patch, list, watch
