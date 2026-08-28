"""
diff_runs.py — compare two program table YAML files and print what changed.

Usage:
    python3 diff_runs.py <previous.yaml> <current.yaml>
"""
import sys
import yaml


def load_mappings(path):
    with open(path) as f:
        return {s["Accession"]: s for s in yaml.safe_load(f)["program_study_mappings"]}


def main():
    if len(sys.argv) != 3:
        print("Usage: diff_runs.py <previous.yaml> <current.yaml>", file=sys.stderr)
        sys.exit(1)

    prev = load_mappings(sys.argv[1])
    curr = load_mappings(sys.argv[2])

    added   = sorted(set(curr) - set(prev))
    removed = sorted(set(prev) - set(curr))
    changed = sorted(
        k for k in set(prev) & set(curr)
        if prev[k].get("program_name") != curr[k].get("program_name")
    )

    print(
        f"Entries: {len(prev)} -> {len(curr)}  "
        f"(+{len(added)} added, -{len(removed)} removed, ~{len(changed)} program-name changes)"
    )
    for a in added:
        print(f"  + {a}: {curr[a].get('program_name', '?')}")
    for r in removed:
        print(f"  - {r}: {prev[r].get('program_name', '?')}")
    for c in changed:
        print(f"  ~ {c}: {prev[c].get('program_name', '?')} -> {curr[c].get('program_name', '?')}")
    if not added and not removed and not changed:
        print("  (no changes detected)")


if __name__ == "__main__":
    main()
