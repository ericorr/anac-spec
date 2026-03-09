#!/usr/bin/env python3
"""Validate ANAC 0.1.2 example manifests against the local core schema."""

import json
import sys
from pathlib import Path
from jsonschema import validate, ValidationError, Draft202012Validator

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
SCHEMA_PATH = ROOT_DIR / "schema" / "anac-core-0.1.2.schema.json"
EXAMPLES = [
    (BASE_DIR / "example-sheetapp-0.1.2.json", "SheetApp (spreadsheet)"),
    (BASE_DIR / "example-vectorforge-0.1.2.json", "VectorForge (vector editor)"),
]

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)

def collect_errors(schema, instance):
    validator = Draft202012Validator(schema)
    return sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))

def main():
    schema = load_json(SCHEMA_PATH)
    print(f"Schema loaded: {schema.get('title', 'unknown')}\n")

    all_passed = True
    for path, label in EXAMPLES:
        print(f"{'='*60}")
        print(f"Validating: {label}")
        print(f"File: {path}")
        print(f"{'='*60}")

        instance = load_json(path)
        errors = collect_errors(schema, instance)

        if not errors:
            print(f"  PASS — no validation errors\n")
            # Print some stats
            entities = instance.get("static", {}).get("entities", [])
            actions = instance.get("static", {}).get("actions", [])
            workflows = instance.get("static", {}).get("workflows", [])
            total_steps = sum(len(w.get("steps", [])) for w in workflows)
            total_subflows = sum(len(w.get("subflows", [])) for w in workflows)
            print(f"  Entities:  {len(entities)}")
            print(f"  Actions:   {len(actions)}")
            print(f"  Workflows: {len(workflows)}")
            print(f"  Steps:     {total_steps}")
            print(f"  Subflows:  {total_subflows}")
            print(f"  Tier:      {instance.get('application', {}).get('tier', '?')}")
            print()
        else:
            all_passed = False
            print(f"  FAIL — {len(errors)} validation error(s):\n")
            for i, err in enumerate(errors, 1):
                path_str = " -> ".join(str(p) for p in err.absolute_path) or "(root)"
                print(f"  [{i}] Path: {path_str}")
                print(f"      Error: {err.message}")
                if err.context:
                    # For oneOf errors, show which sub-schema failed
                    for ctx in err.context[:3]:
                        ctx_path = " -> ".join(str(p) for p in ctx.absolute_path)
                        print(f"        Sub-error at {ctx_path}: {ctx.message}")
                print()

    print("="*60)
    if all_passed:
        print("ALL EXAMPLES PASSED VALIDATION")
    else:
        print("SOME EXAMPLES FAILED — see above")
    print("="*60)
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
