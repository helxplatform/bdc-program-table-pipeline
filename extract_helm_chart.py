#!/usr/bin/env python3
"""
Extract the deployed Helm chart from the cluster's release secret.

Helm v3 stores the full chart (including all sub-chart templates) inside the
release secret as: base64 -> gzip -> JSON.  This script reconstructs a
usable chart directory from that data so helm upgrade can use the exact
chart version that is already running on the cluster.

Usage:
    python3 extract_helm_chart.py --namespace bdc-search-dev \
                                   --release search \
                                   --output /tmp/search-chart
"""

import argparse
import base64
import gzip
import json
import os
import subprocess
import sys


def get_release_json(namespace: str, release: str) -> dict:
    """Fetch and decode the latest Helm release secret from the cluster."""
    result = subprocess.run(
        [
            "kubectl", "get", "secret",
            "-n", namespace,
            "-l", f"owner=helm,name={release}",
            "--sort-by=.metadata.creationTimestamp",
            "-o", "jsonpath={.items[-1].data.release}",
        ],
        capture_output=True, text=True, check=True,
    )
    encoded = result.stdout.strip()
    if not encoded:
        raise RuntimeError(f"No helm release secret found for release '{release}' in namespace '{namespace}'")

    # Helm stores: base64(gzip(base64(json)))
    # First base64 decode gives gzipped base64
    step1 = base64.b64decode(encoded)
    try:
        step2 = gzip.decompress(step1)
    except Exception:
        step2 = step1  # not gzipped — try as-is

    # May be double-encoded: some versions do base64(gzip(base64(json)))
    try:
        step3 = base64.b64decode(step2)
        try:
            step4 = gzip.decompress(step3)
            return json.loads(step4)
        except Exception:
            return json.loads(step3)
    except Exception:
        return json.loads(step2)


def write_chart(chart: dict, out_dir: str):
    """Recursively reconstruct a chart directory from the Helm release chart object."""
    os.makedirs(out_dir, exist_ok=True)

    # Write Chart.yaml
    metadata = chart.get("metadata") or chart.get("Metadata") or {}
    if metadata:
        import yaml
        with open(os.path.join(out_dir, "Chart.yaml"), "w") as f:
            yaml.dump(metadata, f, default_flow_style=False)

    # Write values.yaml
    values = chart.get("values") or chart.get("Values") or {}
    if values:
        import yaml
        with open(os.path.join(out_dir, "values.yaml"), "w") as f:
            yaml.dump(values, f, default_flow_style=False, allow_unicode=True)

    # Write templates
    templates = chart.get("templates") or chart.get("Templates") or []
    if templates:
        tmpl_dir = os.path.join(out_dir, "templates")
        os.makedirs(tmpl_dir, exist_ok=True)
        for t in templates:
            name = t.get("name") or t.get("Name", "")
            data = t.get("data") or t.get("Data", "")
            if name and data:
                # Strip leading "templates/" prefix if present
                rel_name = name
                for prefix in ("templates/", "template/"):
                    if rel_name.startswith(prefix):
                        rel_name = rel_name[len(prefix):]
                        break
                content = base64.b64decode(data)
                fpath = os.path.join(tmpl_dir, rel_name)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, "wb") as f:
                    f.write(content)

    # Write additional files (helpers, notes, etc.)
    files = chart.get("files") or chart.get("Files") or []
    for fi in files:
        name = fi.get("name") or fi.get("Name", "")
        data = fi.get("data") or fi.get("Data", "")
        if name and data:
            content = base64.b64decode(data)
            fpath = os.path.join(out_dir, name)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "wb") as f:
                f.write(content)

    # Recursively write sub-charts (dependencies)
    dependencies = chart.get("dependencies") or chart.get("Dependencies") or []
    if dependencies:
        charts_dir = os.path.join(out_dir, "charts")
        os.makedirs(charts_dir, exist_ok=True)
        for dep in dependencies:
            dep_meta = dep.get("metadata") or dep.get("Metadata") or {}
            dep_name = dep_meta.get("name") or dep_meta.get("Name", "dep")
            dep_out = os.path.join(charts_dir, dep_name)
            write_chart(dep, dep_out)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--namespace", default="bdc-search-dev")
    parser.add_argument("--release",   default="search")
    parser.add_argument("--output",    required=True, help="Output chart directory")
    args = parser.parse_args()

    print(f"Fetching helm release '{args.release}' from namespace '{args.namespace}'...")
    release = get_release_json(args.namespace, args.release)

    chart = release.get("chart") or release.get("Chart")
    if not chart:
        print("ERROR: No chart found in release secret", file=sys.stderr)
        sys.exit(1)

    meta = chart.get("metadata") or chart.get("Metadata") or {}
    print(f"Chart: {meta.get('name')} {meta.get('version')}")

    print(f"Writing chart to: {args.output}")
    write_chart(chart, args.output)
    print("Done.")


if __name__ == "__main__":
    main()
