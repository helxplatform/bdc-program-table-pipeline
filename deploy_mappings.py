#!/usr/bin/env python3
"""
Deploy updated program table YAML to the Helm chart values file.

Shows a diff summary (added/removed/changed studies) and asks for confirmation
before overwriting the target file.

Usage:
    python deploy_mappings.py --input <pipeline_yaml> --target <bdc-dev-mappings.yaml>

Example:
    python deploy_mappings.py \\
        --input Aug_2026/Aug_28_2026/program_table/program_table_updated_20260826_155351.yaml \\
        --target /Users/ykale/helxcharts/search-chart/bdc-dev-values/bdc-dev-mappings.yaml
"""

import argparse
import sys
import yaml
from collections import defaultdict


def load_mappings_yaml(path):
    """Load program_study_mappings from a pipeline YAML (bare list) or values YAML (under config:)."""
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    if isinstance(data, dict):
        # Could be config.program_study_mappings (values file) or program_study_mappings (pipeline output)
        if 'config' in data:
            return data['config']['program_study_mappings']
        elif 'program_study_mappings' in data:
            return data['program_study_mappings']
    raise ValueError(f"Unrecognized YAML structure in {path}")


def base_accession(accession):
    return accession.split('.')[0] if accession else accession


def build_diff(old_list, new_list):
    old_idx = {s['Accession']: s for s in old_list}
    new_idx = {s['Accession']: s for s in new_list}
    old_acc = set(old_idx)
    new_acc = set(new_idx)

    added_acc = sorted(new_acc - old_acc)
    removed_acc = sorted(old_acc - new_acc)

    # Distinguish version bumps from truly new/removed
    added_bases = {base_accession(a): a for a in added_acc}
    removed_bases = {base_accession(a): a for a in removed_acc}
    bump_bases = set(added_bases) & set(removed_bases)

    version_bumps = []
    truly_added = []
    truly_removed = []

    for b in sorted(bump_bases):
        old_entries = [a for a in removed_acc if base_accession(a) == b]
        new_entries = [a for a in added_acc if base_accession(a) == b]
        version_bumps.append((b, old_entries, new_entries, new_idx[new_entries[0]]))

    for a in added_acc:
        if base_accession(a) not in bump_bases:
            truly_added.append(a)

    for a in removed_acc:
        if base_accession(a) not in bump_bases:
            truly_removed.append(a)

    # Field changes for common accessions
    key_fields = ['Program', 'NHLBI Program Name', 'Study Name',
                  'Released', 'Subjects Count', 'Community', 'Community1', 'Community2']
    changed = defaultdict(list)
    for acc in sorted(old_acc & new_acc):
        o = old_idx[acc]
        n = new_idx[acc]
        diffs = {k: (o.get(k, ''), n.get(k, '')) for k in key_fields if o.get(k, '') != n.get(k, '')}
        if diffs:
            changed[base_accession(acc)].append((acc, diffs))

    return {
        'old_count': len(old_list),
        'new_count': len(new_list),
        'version_bumps': version_bumps,
        'truly_added': truly_added,
        'truly_removed': truly_removed,
        'changed': changed,
        'new_idx': new_idx,
        'old_idx': old_idx,
    }


def print_diff(diff):
    old_count = diff['old_count']
    new_count = diff['new_count']
    delta = new_count - old_count
    sign = f'+{delta}' if delta >= 0 else str(delta)

    print("=" * 80)
    print("PROGRAM TABLE DIFF SUMMARY")
    print("=" * 80)
    print(f"  Entries: {old_count} -> {new_count}  ({sign})")

    bumps = diff['version_bumps']
    added = diff['truly_added']
    removed = diff['truly_removed']
    changed = diff['changed']
    new_idx = diff['new_idx']
    old_idx = diff['old_idx']

    # Group truly added by base accession
    added_by_base = defaultdict(list)
    for a in added:
        added_by_base[base_accession(a)].append(a)

    removed_by_base = defaultdict(list)
    for a in removed:
        removed_by_base[base_accession(a)].append(a)

    print(f"\n  New studies added:   {len(added_by_base)} unique ({len(added)} consent records)")
    print(f"  Studies removed:     {len(removed_by_base)} unique ({len(removed)} consent records)")
    print(f"  Version bumps:       {len(bumps)} studies")
    print(f"  Field changes:       {len(changed)} unique studies")

    # Break down Released-only vs other field changes
    released_only = sum(
        1 for accs in changed.values()
        if all(set(diffs.keys()) == {'Released'} for _, diffs in accs)
    )
    other_changes = len(changed) - released_only
    print(f"    - Released status only: {released_only}")
    print(f"    - Other field changes:  {other_changes}")

    if bumps:
        print(f"\n--- VERSION BUMPS ({len(bumps)}) ---")
        for base, old_entries, new_entries, study in bumps:
            name = study.get('Study Name', '')[:55]
            old_v = ', '.join(old_entries)
            new_v = ', '.join(new_entries)
            print(f"  {base}  {old_v} -> {new_v}")
            print(f"    {name}")

    if added_by_base:
        print(f"\n--- NEW STUDIES ({len(added_by_base)}) ---")
        for base in sorted(added_by_base):
            entries = added_by_base[base]
            s = new_idx[entries[0]]
            name = s.get('Study Name', '')[:55]
            prog = s.get('Program', '')
            print(f"  + {base} ({len(entries)} consents)  [{prog}]  {name}")

    if removed_by_base:
        print(f"\n--- REMOVED STUDIES ({len(removed_by_base)}) ---")
        for base in sorted(removed_by_base):
            entries = removed_by_base[base]
            s = old_idx[entries[0]]
            name = s.get('Study Name', '')[:55]
            prog = s.get('Program', '')
            print(f"  - {base} ({len(entries)} consents)  [{prog}]  {name}")

    if other_changes > 0:
        print(f"\n--- OTHER FIELD CHANGES (non-Released) ---")
        for base in sorted(changed):
            accs = changed[base]
            # Check if any have non-Released changes
            non_released = [(acc, {k: v for k, v in diffs.items() if k != 'Released'})
                            for acc, diffs in accs if any(k != 'Released' for k in diffs)]
            if not non_released:
                continue
            acc0, diffs0 = non_released[0]
            s = new_idx[acc0]
            name = s.get('Study Name', '')[:55]
            print(f"\n  {base} [{name}]  ({len(accs)} records)")
            for field, (old_val, new_val) in sorted(diffs0.items()):
                print(f"    {field}: '{old_val}' -> '{new_val}'")

    print()


def build_output_yaml(new_list):
    """Build the full bdc-dev-mappings.yaml content from the new mappings list."""
    inner = yaml.dump(
        {'program_study_mappings': new_list},
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=10000,
    )
    # Indent everything 2 spaces under config:
    indented = '\n'.join('  ' + line if line.strip() else line for line in inner.splitlines())
    return 'config:\n' + indented + '\n'


def main():
    parser = argparse.ArgumentParser(
        description='Deploy program table YAML to Helm chart values file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--input', required=True,
                        help='New pipeline YAML (program_table_updated_*.yaml)')
    parser.add_argument('--target', required=True,
                        help='Target Helm chart values file (bdc-dev-mappings.yaml)')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip confirmation prompt and update immediately')
    args = parser.parse_args()

    # Load both files
    try:
        new_list = load_mappings_yaml(args.input)
    except Exception as e:
        print(f"Error reading input file: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        old_list = load_mappings_yaml(args.target)
    except Exception as e:
        print(f"Error reading target file: {e}", file=sys.stderr)
        sys.exit(1)

    # Show diff
    diff = build_diff(old_list, new_list)
    print_diff(diff)

    print(f"Input:  {args.input}")
    print(f"Target: {args.target}")
    print()

    # Confirm
    if not args.yes:
        answer = input("Update target file? [y/N] ").strip().lower()
        if answer not in ('y', 'yes'):
            print("Aborted.")
            sys.exit(0)

    # Write
    output = build_output_yaml(new_list)
    with open(args.target, 'w', encoding='utf-8') as f:
        f.write(output)

    entry_count = sum(1 for s in new_list if s.get('Accession'))
    print(f"Updated {args.target}  ({entry_count} entries)")


if __name__ == '__main__':
    main()
