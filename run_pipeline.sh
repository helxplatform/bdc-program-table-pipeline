#!/bin/bash
#
# BDC Program Table Pipeline
#
# Fetches study data from BDC, Gen3, and Jira, generates the program
# table, and optionally deploys it to the Helm chart values file and cluster.
#
# Usage:
#   ./run_pipeline.sh [OPTIONS]
#
# Options:
#   -o, --output-dir DIR        Base output directory (auto-creates dated subdir)
#   -j, --jira-file FILE        Jira CSV file (skips Jira API fetch if provided)
#   -s, --step STEP             Run specific step: 0, 1, 2, 3, or all (default: all)
#                                 0 = Fetch Jira data via API
#                                 1 = Fetch studies from BDC + Gen3
#                                 2 = Update program names from Jira
#                                 3 = Deploy to Helm + restart search-api
#   --bdc-url URL               BDC API URL (optional)
#   --gen3-url URL              Gen3 API URL (optional)
#   --jira-url URL              Jira base URL (env: JIRA_URL)
#   --jira-token TOKEN          Jira PAT (env: JIRA_TOKEN)
#   --target-mappings FILE      Helm values file to update (env: TARGET_MAPPINGS)
#   --helm-release NAME         Helm release name (default: search)
#   --helm-namespace NS         Kubernetes namespace (default: bdc-search-dev)
#   --helm-values FILE          Helm values file for upgrade (env: HELM_VALUES_FILE)
#   --skip-deploy               Run steps 0-2 only, skip Helm deploy
#   -h, --help                  Show this help message
#
# Environment variables (for pod/CI use):
#   JIRA_URL          Jira base URL
#   JIRA_TOKEN        Jira Personal Access Token
#   TARGET_MAPPINGS   Path to bdc-dev-mappings.yaml
#   HELM_VALUES_FILE  Path to bdc-dev-values.yaml for helm upgrade
#
# Examples:
#   # Full automated pipeline (fetches Jira via API, deploys to cluster)
#   JIRA_TOKEN=mytoken ./run_pipeline.sh -o /data/runs
#
#   # Use existing Jira CSV, skip deploy
#   ./run_pipeline.sh -o /data/runs -j /path/to/jira.csv --skip-deploy
#
#   # Step 0 only: fetch Jira data
#   JIRA_TOKEN=mytoken ./run_pipeline.sh -o /data/runs -s 0
#
#   # Step 3 only: deploy existing output
#   ./run_pipeline.sh -o /data/runs/Aug_28_2026 -s 3
#

set -e
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Defaults ────────────────────────────────────────────────────────────────
OUTPUT_BASE=""
JIRA_FILE=""
STEP="all"
BDC_URL="https://search.biodatacatalyst.renci.org/search-api"
GEN3_URL="https://gen3.biodatacatalyst.nhlbi.nih.gov/"
JIRA_URL="${JIRA_URL:-https://nhlbijira.nhlbi.nih.gov}"
JIRA_TOKEN="${JIRA_TOKEN:-}"
TARGET_MAPPINGS="${TARGET_MAPPINGS:-}"
HELM_VALUES_FILE="${HELM_VALUES_FILE:-}"
HELM_RELEASE="search"
HELM_NAMESPACE="bdc-search-dev"
SKIP_DEPLOY=false

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

print_header()  { echo -e "${BLUE}$(printf '=%.0s' {1..100})${NC}"; echo -e "${BLUE}$1${NC}"; echo -e "${BLUE}$(printf '=%.0s' {1..100})${NC}"; }
print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_error()   { echo -e "${RED}✗ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_info()    { echo -e "$1"; }

show_help() { sed -n '2,52p' "$0" | sed 's/^# //' | sed 's/^#//'; exit 0; }

# ── Argument parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output-dir)       OUTPUT_BASE="$2";        shift 2 ;;
        -j|--jira-file)        JIRA_FILE="$2";          shift 2 ;;
        -s|--step)             STEP="$2";               shift 2 ;;
        --bdc-url)             BDC_URL="$2";            shift 2 ;;
        --gen3-url)            GEN3_URL="$2";           shift 2 ;;
        --jira-url)            JIRA_URL="$2";           shift 2 ;;
        --jira-token)          JIRA_TOKEN="$2";         shift 2 ;;
        --target-mappings)     TARGET_MAPPINGS="$2";    shift 2 ;;
        --helm-release)        HELM_RELEASE="$2";       shift 2 ;;
        --helm-namespace)      HELM_NAMESPACE="$2";     shift 2 ;;
        --helm-values)         HELM_VALUES_FILE="$2";   shift 2 ;;
        --skip-deploy)         SKIP_DEPLOY=true;        shift ;;
        -h|--help)             show_help ;;
        *) print_error "Unknown option: $1"; echo "Use -h for help"; exit 1 ;;
    esac
done

# ── Validate ─────────────────────────────────────────────────────────────────
if [[ -z "$OUTPUT_BASE" ]]; then
    print_error "Output directory is required (-o or --output-dir)"
    exit 1
fi

# Auto-create dated subdirectory (e.g. Aug_28_2026) unless a specific step
# is being re-run into an existing directory
DATE_DIR="$(date '+%b_%d_%Y')"
if [[ "$STEP" == "all" || "$STEP" == "0" || "$STEP" == "1" ]]; then
    OUTPUT_DIR="${OUTPUT_BASE}/${DATE_DIR}"
else
    # For step 2/3 re-runs, use the most recent dated subdir if OUTPUT_BASE
    # itself doesn't contain a program_table directory
    if [[ -d "$OUTPUT_BASE/program_table" ]]; then
        OUTPUT_DIR="$OUTPUT_BASE"
    else
        LATEST=$(ls -td "${OUTPUT_BASE}"/*/  2>/dev/null | head -1 | sed 's|/$||')
        OUTPUT_DIR="${LATEST:-$OUTPUT_BASE}"
    fi
fi

mkdir -p "$OUTPUT_DIR"

# ── Step 0 validation ─────────────────────────────────────────────────────────
if [[ ("$STEP" == "all" || "$STEP" == "0" || "$STEP" == "2") && -z "$JIRA_FILE" ]]; then
    if [[ "$STEP" == "0" || ("$STEP" == "all" && -z "$JIRA_TOKEN") ]]; then
        if [[ -z "$JIRA_TOKEN" ]]; then
            print_error "Jira token required for API fetch. Use --jira-token or set JIRA_TOKEN."
            exit 1
        fi
    fi
fi

# ── Step 3 validation ─────────────────────────────────────────────────────────
WILL_DEPLOY=false
if [[ "$SKIP_DEPLOY" == false && ("$STEP" == "all" || "$STEP" == "3") ]]; then
    WILL_DEPLOY=true
    if [[ -z "$TARGET_MAPPINGS" ]]; then
        print_error "Target mappings file required for deploy. Use --target-mappings or set TARGET_MAPPINGS."
        exit 1
    fi
    if [[ ! -f "$TARGET_MAPPINGS" ]]; then
        print_error "Target mappings file not found: $TARGET_MAPPINGS"
        exit 1
    fi
    if [[ -z "$HELM_VALUES_FILE" ]]; then
        print_error "Helm values file required for deploy. Use --helm-values or set HELM_VALUES_FILE."
        exit 1
    fi
    if ! command -v helm &>/dev/null; then
        print_error "helm not found in PATH"
        exit 1
    fi
    if ! command -v kubectl &>/dev/null; then
        print_error "kubectl not found in PATH"
        exit 1
    fi
fi

# ── Print config ──────────────────────────────────────────────────────────────
print_header "BDC Program Table Pipeline"
print_info "Configuration:"
print_info "  Output directory: $OUTPUT_DIR"
print_info "  BDC URL:          $BDC_URL"
print_info "  Gen3 URL:         $GEN3_URL"
if [[ -n "$JIRA_FILE" ]]; then
    print_info "  Jira source:      file ($JIRA_FILE)"
elif [[ -n "$JIRA_TOKEN" ]]; then
    print_info "  Jira source:      API ($JIRA_URL)"
fi
if [[ "$WILL_DEPLOY" == true ]]; then
    print_info "  Deploy target:    $TARGET_MAPPINGS"
    print_info "  Helm release:     $HELM_RELEASE  namespace: $HELM_NAMESPACE"
fi
[[ "$STEP" != "all" ]] && print_info "  Step: $STEP"
echo ""

# ── Step 0: Fetch Jira data via API ──────────────────────────────────────────
if [[ "$STEP" == "all" || "$STEP" == "0" ]]; then
    if [[ -n "$JIRA_FILE" ]]; then
        print_info "Skipping step 0 — using provided Jira file: $JIRA_FILE"
    else
        print_header "STEP 0: Fetching Jira Program Mappings via API"
        JIRA_OUT="$OUTPUT_DIR/jira_program_mappings.csv"

        if python3 "$SCRIPT_DIR/fetch_jira_data.py" \
            --jira-url  "$JIRA_URL" \
            --jira-token "$JIRA_TOKEN" \
            --output    "$JIRA_OUT"; then
            JIRA_FILE="$JIRA_OUT"
            print_success "Step 0 completed — Jira data saved to: $JIRA_FILE"
            echo ""
        else
            print_error "Step 0 failed"
            exit 1
        fi
    fi
fi

# ── Step 1: Fetch studies from BDC + Gen3 ────────────────────────────────────
if [[ "$STEP" == "all" || "$STEP" == "1" ]]; then
    print_header "STEP 1: Fetching Studies from BDC and Gen3"

    if python3 "$SCRIPT_DIR/fetch_studies.py" \
        --output-dir "$OUTPUT_DIR" \
        --bdc-url    "$BDC_URL" \
        --gen3-url   "$GEN3_URL"; then
        print_success "Step 1 completed successfully"
        echo ""
    else
        print_error "Step 1 failed"
        exit 1
    fi
fi

# ── Step 2: Update program names from Jira ───────────────────────────────────
if [[ "$STEP" == "all" || "$STEP" == "2" ]]; then
    print_header "STEP 2: Updating Program Names from Jira"

    INPUT_FILE="$OUTPUT_DIR/program_table/program_table.json"
    if [[ ! -f "$INPUT_FILE" ]]; then
        print_error "Input file not found: $INPUT_FILE"
        print_error "Run step 1 first."
        exit 1
    fi

    if [[ -z "$JIRA_FILE" ]]; then
        print_error "No Jira data available. Run step 0 first or provide --jira-file."
        exit 1
    fi

    if python3 "$SCRIPT_DIR/update_programs.py" \
        --jira-file   "$JIRA_FILE" \
        --input-file  "$INPUT_FILE" \
        --output-dir  "$OUTPUT_DIR"; then
        print_success "Step 2 completed successfully"
        echo ""
    else
        print_error "Step 2 failed"
        exit 1
    fi
fi

# ── Step 3: Deploy to Helm + restart search-api ───────────────────────────────
if [[ "$WILL_DEPLOY" == true ]]; then
    print_header "STEP 3: Deploying to Helm"

    # Find the final program table YAML from step 2
    UPDATED_YAML=$(ls -t "$OUTPUT_DIR/program_table/program_table_updated_"*.yaml 2>/dev/null | grep -v minified | head -1)
    if [[ -z "$UPDATED_YAML" ]]; then
        print_error "No updated program table YAML found in $OUTPUT_DIR/program_table/"
        exit 1
    fi
    print_info "  Program table: $UPDATED_YAML"

    # 3a — Update bdc-dev-mappings.yaml (with diff summary + auto-confirm)
    print_info ""
    print_info "Updating $TARGET_MAPPINGS ..."
    if python3 "$SCRIPT_DIR/deploy_mappings.py" \
        --input  "$UPDATED_YAML" \
        --target "$TARGET_MAPPINGS" \
        --yes; then
        print_success "Mappings file updated"
    else
        print_error "deploy_mappings.py failed"
        exit 1
    fi

    # 3b — Verify entry count
    ENTRY_COUNT=$(grep -c "Accession:" "$TARGET_MAPPINGS" || true)
    print_info "  Verified: $ENTRY_COUNT entries in mappings file"

    # 3c — Helm upgrade
    print_info ""
    print_info "Running helm upgrade ..."
    HELM_CHART_DIR="$(dirname "$(dirname "$TARGET_MAPPINGS")")"
    if helm upgrade "$HELM_RELEASE" "$HELM_CHART_DIR" \
        -n "$HELM_NAMESPACE" \
        -f "$HELM_VALUES_FILE" \
        -f "$TARGET_MAPPINGS" \
        --reuse-values; then
        print_success "Helm upgrade completed"
    else
        print_error "Helm upgrade failed"
        exit 1
    fi

    # 3d — Restart search-api
    print_info ""
    print_info "Restarting search-api deployment ..."
    if kubectl rollout restart deployment/search-api -n "$HELM_NAMESPACE"; then
        kubectl rollout status deployment/search-api -n "$HELM_NAMESPACE" --timeout=120s
        print_success "search-api restarted"
    else
        print_error "kubectl rollout restart failed"
        exit 1
    fi

    # 3e — Verify ConfigMap
    print_info ""
    print_info "Verifying ConfigMap ..."
    CM_COUNT=$(kubectl get configmap -n "$HELM_NAMESPACE" search-api-program-study-mappings \
        -o jsonpath='{.data.program_study_mappings\.json}' 2>/dev/null | \
        python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "unknown")
    print_info "  ConfigMap entries: $CM_COUNT  (mappings file: $ENTRY_COUNT)"

    if [[ "$CM_COUNT" == "$ENTRY_COUNT" ]]; then
        print_success "ConfigMap verified — $CM_COUNT entries"
    else
        print_warning "Entry count mismatch: file=$ENTRY_COUNT, ConfigMap=$CM_COUNT"
        print_warning "The pod may still be restarting — re-check in a moment."
    fi

    echo ""
fi

# ── Final summary ─────────────────────────────────────────────────────────────
print_header "Pipeline Completed Successfully!"
print_info ""
print_info "Output directory: $OUTPUT_DIR"
print_info ""
print_info "Key files:"
[[ "$STEP" == "all" || "$STEP" == "0" ]] && [[ -n "$JIRA_FILE" ]] && \
    print_info "  Jira data:       $JIRA_FILE"
[[ "$STEP" == "all" || "$STEP" == "1" ]] && \
    print_info "  BDC studies:     $OUTPUT_DIR/studies_on_bdc_portal/bdc_studies.json" && \
    print_info "  Gen3 studies:    $OUTPUT_DIR/studies_on_gen3_portal/raw_studies_on_gen3.json"
[[ "$STEP" == "all" || "$STEP" == "2" ]] && \
    print_info "  Program table:   $OUTPUT_DIR/program_table/program_table_updated_*.json"
[[ "$WILL_DEPLOY" == true ]] && \
    print_info "  Deployed to:     $TARGET_MAPPINGS  ($HELM_RELEASE / $HELM_NAMESPACE)"
print_info ""
print_success "All done!"
