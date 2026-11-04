# BDC Program Table Pipeline

Automated pipeline that fetches BioData Catalyst (BDC) study data from Jira, the BDC portal, and Gen3, merges program name mappings, and deploys the result to the search API on the sterling Kubernetes cluster.

---

## Table of Contents

- [Overview](#overview)
- [Data Flow](#data-flow)
- [Pipeline Steps](#pipeline-steps)
- [Jira Field Mapping](#jira-field-mapping)
- [Program Assignment Logic](#program-assignment-logic)
- [Running Locally](#running-locally)
- [Docker Image](#docker-image)

---

## Overview

This pipeline maintains the BDC search portal's program-study mapping table. When a curator updates a study's program assignment in Jira, the pipeline picks up the change, validates it against the live BDC program list, and publishes the updated mappings to the search API. The search UI reflects the changes immediately — no manual intervention required.

---

## Data Flow

```
NHLBI Jira (BDC-DMC Joint Workspace)
    │  Accession, Program(s), Gen3 Program Name, Community
    │
    ▼ Step 0 — fetch_jira_data.py
jira_program_mappings.csv
    │
    ├── Step 1 — fetch_studies.py ◄── BDC portal API + Gen3 API
    │       bdc_studies.json
    │       raw_studies_on_gen3.json
    │
    ▼ Step 2 — update_programs.py
program_table_updated_<date>.yaml
    │
    ▼ Step 3 — deploy
search-api-program-study-mappings  ──► search-api serves updated mappings to the UI
```

---

## Pipeline Steps

| Step | Script | Description |
|------|--------|-------------|
| 0 | `fetch_jira_data.py` | Fetch program mappings from Jira (BDC-DMC Joint Workspace, `Dataset` issues in status `DATA AVAILABLE`) |
| 1 | `fetch_studies.py` | Fetch the current study list from BDC portal and Gen3 |
| 2 | `update_programs.py` | Merge Jira mappings into the study list and validate program names against BDC |
| 3 | *(deploy)* | Push the updated program-study mapping to the search API |

---

## Jira Field Mapping

`fetch_jira_data.py` queries the **BDC-DMC Joint Workspace** project using the following filter:

```
project = "BDC-DMC Joint Workspace"
AND issuetype = Dataset
AND status = "DATA AVAILABLE"
```

Each issue field is mapped as follows:

| Jira Field | API Field ID | Output Column | Description |
|------------|--------------|---------------|-------------|
| Accession | `customfield_15206` | `Accession` | dbGaP accession number (e.g. `phs000007.v32.p13`) |
| Program(s) | `customfield_15909` | `Program(s)` | NHLBI program name — primary source for program assignment |
| Gen3 Program Name | `customfield_15209` | `Gen3 Program Name` | Gen3-side program label |
| Community | `customfield_16100` | `Community`, `Community.1`, `Community.2` | BDC community tags (multi-select, up to 3 values) |
| Status | `status` | `Status` | Issue status |
| Summary | `summary` | `Summary` | Issue title / study name |

Issues without an Accession value are skipped during the merge step.

---

## Program Assignment Logic

`update_programs.py` resolves the final program for each study using the following priority order:

1. **Jira `Program(s)` field** — used first if populated.
2. **First `Community` value** — used as a fallback if `Program(s)` is empty.
3. **Validation against BDC programs** — the resolved name is checked against the live BDC program list:
   - Recognized program: the correctly-cased BDC name is used.
   - Unrecognized program: the study is assigned to `Extramural Research`.
   - Empty or `tutorial`: the study is excluded from the output.

Studies belonging to multiple communities receive one entry per community in the program table, allowing them to appear under each relevant program filter in the search UI. A study is never duplicated under the same program twice.

Studies not found in Jira retain their existing program assignment from the BDC portal.

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

Output is written to `/path/to/output/<YYYY_Mon_DD>/`:

```
<output>/
  <date>/
    jira_program_mappings.csv
    studies_on_bdc_portal/
      bdc_studies.json
    studies_on_gen3_portal/
      raw_studies_on_gen3.json
    program_table/
      program_table_updated_<timestamp>.yaml
      program_table_updated_<timestamp>.json
      program_table_updated_<timestamp>_minified.json
```

### Run full pipeline including deploy

```bash
bash run_pipeline.sh -o /path/to/output
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `-o <dir>` | Base output directory (required) |
| `--skip-deploy` | Run steps 0–2 only; skip the deploy step |
| `--jira-token <tok>` | Override the `$JIRA_TOKEN` environment variable |

---

## Docker Image

The pipeline image is published to:

```
containers.renci.org/helxplatform/bdc-program-table-pipeline:latest
```

The image is built and pushed automatically via GitHub Actions on every merge to `main`. It bundles the Python pipeline scripts together with the tools required for the deploy step.
