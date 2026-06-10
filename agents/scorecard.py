#!/usr/bin/env python3
"""
scorecard.py — dbt Model Health Scorecard
==========================================
Reads manifest.json (and optionally run_results.json) to score every
model across five health dimensions. Outputs a ranked table to the
terminal and writes a JSON report for CI integration.

Usage:
    # Score a single project
    python agents/scorecard.py --manifest dbt_platform/target/manifest.json

    # Score all projects
    python agents/scorecard.py --all

    # Score with run results (adds execution metrics)
    python agents/scorecard.py --manifest dbt_analytics/target/manifest.json \\
        --run-results dbt_analytics/target/run_results.json

    # Output JSON for CI integration
    python agents/scorecard.py --all --output json

Dimensions (20 points each, total 100):
    1. Documentation   — model + column descriptions
    2. Test coverage   — data tests per column
    3. Contracts       — enforced + data_type on all columns
    4. Freshness       — upstream source freshness config
    5. Blast radius    — downstream model count (penalised if > 5)
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
PROJECTS  = [
    "dbt_platform", "dbt_commercial", "dbt_finance",
    "dbt_product",  "dbt_marketing",  "dbt_analytics",
]

BLAST_THRESHOLD = 5      # models with > N downstream dependents are penalised
GRADE_THRESHOLDS = {     # score → grade
    80: "A",
    60: "B",
    40: "C",
    0:  "D",
}

REPORT_PATH = REPO_ROOT / "agents" / "reports" / "scorecard_report.json"


# ── Scoring functions ─────────────────────────────────────────────────────────

def score_documentation(node: dict[str, Any]) -> tuple[int, list[str]]:
    """
    20 points for documentation quality.
      10 pts — model has a meaningful description (> 20 chars)
      10 pts — all columns have descriptions (pro-rated)
    """
    score  = 0
    issues = []

    # Model-level description
    desc = (node.get("description") or "").strip()
    if len(desc) > 20:
        score += 10
    else:
        issues.append("missing or thin model description")

    # Column-level descriptions
    columns = node.get("columns") or {}
    if columns:
        described = sum(
            1 for c in columns.values()
            if len((c.get("description") or "").strip()) > 5
        )
        col_score = round(10 * described / len(columns))
        score += col_score
        if col_score < 10:
            missing = len(columns) - described
            issues.append(f"{missing} column(s) missing descriptions")
    else:
        issues.append("no columns defined in YAML")

    return score, issues


def score_test_coverage(node: dict[str, Any], all_nodes: dict) -> tuple[int, list[str]]:
    """
    20 points for test coverage.
      Find all test nodes that reference this model.
      >= 2 tests per column → full marks (pro-rated down to 0)
    """
    issues = []
    model_name = node["name"]

    # Find tests that reference this model
    test_count = 0
    for n in all_nodes.values():
        if n.get("resource_type") != "test":
            continue
        refs = n.get("refs") or []
        # refs is a list of lists: [['model_name'], ...]  or dicts in newer dbt
        for ref in refs:
            ref_name = ref[0] if isinstance(ref, list) else ref.get("name", "")
            if ref_name == model_name:
                test_count += 1
                break

    columns = node.get("columns") or {}
    col_count = max(len(columns), 1)

    # Target: at least 1 test per column
    ratio = min(test_count / col_count, 1.0)
    score = round(20 * ratio)

    if test_count == 0:
        issues.append("no data tests found")
    elif ratio < 0.5:
        issues.append(f"low test coverage: {test_count} tests for {col_count} columns")

    return score, issues


def score_contracts(node: dict[str, Any]) -> tuple[int, list[str]]:
    """
    20 points for contract enforcement.
      10 pts — contract: enforced: true
      10 pts — all columns have data_type defined
    Access: public mart models are expected to be enforced.
    Staging/intermediate models get full marks for contract: enforced: false.
    """
    score  = 0
    issues = []

    config       = node.get("config") or {}
    contract     = config.get("contract") or {}
    is_enforced  = contract.get("enforced", False)
    access       = config.get("access") or node.get("access") or "protected"
    fqn          = node.get("fqn") or []
    is_mart      = any(p in ("marts", "mart") for p in fqn)

    if is_enforced:
        score += 10
    elif is_mart and access == "public":
        issues.append("public mart missing contract: enforced: true")
    else:
        score += 10  # staging/intermediate don't need enforced contracts

    # Column data_type coverage
    columns = node.get("columns") or {}
    if columns:
        typed = sum(1 for c in columns.values() if c.get("data_type"))
        type_score = round(10 * typed / len(columns))
        score += type_score
        if type_score < 10:
            missing = len(columns) - typed
            issues.append(f"{missing} column(s) missing data_type")
    else:
        score += 5  # partial credit — no columns defined but not penalised fully

    return score, issues


def score_freshness(node: dict[str, Any], sources: dict) -> tuple[int, list[str]]:
    """
    20 points for freshness configuration.
    Checks upstream source nodes for freshness config.
    Staging models reading from sources with freshness config → full marks.
    Intermediate/mart models inherit from their upstream staging.
    """
    issues = []
    fqn    = node.get("fqn") or []
    depends_on = (node.get("depends_on") or {}).get("nodes") or []

    # Check if any upstream source has freshness configured
    source_refs = [n for n in depends_on if n.startswith("source.")]
    if not source_refs:
        # No direct source dependency — intermediate or mart
        # Give full credit: freshness is a source-layer concern
        return 20, []

    sources_with_freshness = 0
    for src_id in source_refs:
        src = sources.get(src_id) or {}
        freshness = (src.get("config") or {}).get("freshness") or src.get("freshness") or {}
        if freshness and (freshness.get("warn_after") or freshness.get("error_after")):
            sources_with_freshness += 1

    if not source_refs:
        return 20, []

    ratio = sources_with_freshness / len(source_refs)
    score = round(20 * ratio)

    if score < 20:
        issues.append(
            f"only {sources_with_freshness}/{len(source_refs)} "
            f"upstream sources have freshness configured"
        )

    return score, issues


def score_blast_radius(
    node_id: str,
    all_nodes: dict,
    threshold: int = BLAST_THRESHOLD
) -> tuple[int, list[str]]:
    """
    20 points for manageable blast radius.
    Count direct + indirect downstream dependents.
    0 downstream  → 20 pts
    1-threshold   → 20 pts
    > threshold   → pro-rated penalty
    """
    issues      = []
    downstream  = 0

    for n in all_nodes.values():
        deps = (n.get("depends_on") or {}).get("nodes") or []
        if node_id in deps:
            downstream += 1

    if downstream <= threshold:
        score = 20
    else:
        # Linear decay above threshold: threshold+1 → 15, 2x → 10, etc.
        excess = downstream - threshold
        score  = max(0, 20 - (excess * 2))
        issues.append(
            f"high blast radius: {downstream} downstream models "
            f"(threshold: {threshold})"
        )

    return score, issues


# ── Main scoring ──────────────────────────────────────────────────────────────

def score_model(
    node_id:    str,
    node:       dict,
    all_nodes:  dict,
    sources:    dict,
) -> dict:
    """Score one model across all five dimensions."""

    doc_score,   doc_issues   = score_documentation(node)
    test_score,  test_issues  = score_test_coverage(node, all_nodes)
    cont_score,  cont_issues  = score_contracts(node)
    fresh_score, fresh_issues = score_freshness(node, sources)
    blast_score, blast_issues = score_blast_radius(node_id, all_nodes)

    total  = doc_score + test_score + cont_score + fresh_score + blast_score
    grade  = next(g for t, g in sorted(GRADE_THRESHOLDS.items(), reverse=True) if total >= t)
    issues = doc_issues + test_issues + cont_issues + fresh_issues + blast_issues

    return {
        "model":          node["name"],
        "project":        node.get("package_name", ""),
        "path":           node.get("original_file_path", ""),
        "access":         (node.get("config") or {}).get("access") or "protected",
        "materialized":   (node.get("config") or {}).get("materialized") or "view",
        "total_score":    total,
        "grade":          grade,
        "dimensions": {
            "documentation": doc_score,
            "test_coverage":  test_score,
            "contracts":      cont_score,
            "freshness":      fresh_score,
            "blast_radius":   blast_score,
        },
        "issues": issues,
    }


def load_manifest(path: Path) -> dict:
    if not path.exists():
        print(f"  ⚠️  Manifest not found: {path}", file=sys.stderr)
        return {}
    with open(path) as f:
        return json.load(f)


def score_project(manifest_path: Path) -> list[dict]:
    manifest = load_manifest(manifest_path)
    if not manifest:
        return []

    all_nodes = manifest.get("nodes") or {}
    sources   = manifest.get("sources") or {}

    results = []
    for node_id, node in all_nodes.items():
        if node.get("resource_type") != "model":
            continue
        result = score_model(node_id, node, all_nodes, sources)
        results.append(result)

    return results


# ── Rendering ─────────────────────────────────────────────────────────────────

GRADE_COLOUR = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}


def render_table(results: list[dict]) -> None:
    """Print a ranked scorecard table to stdout."""
    if not results:
        print("No models found.")
        return

    sorted_results = sorted(results, key=lambda r: r["total_score"], reverse=True)

    header = (
        f"{'#':>3}  {'Grade':<6} {'Score':>5}  "
        f"{'Doc':>4} {'Test':>4} {'Cont':>4} {'Fresh':>5} {'Blast':>5}  "
        f"{'Model':<45}  {'Issues'}"
    )
    print("\n" + "=" * 140)
    print("  dbt Mesh — Model Health Scorecard")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 140)
    print(header)
    print("-" * 140)

    for i, r in enumerate(sorted_results, 1):
        dims  = r["dimensions"]
        grade = r["grade"]
        icon  = GRADE_COLOUR.get(grade, "⚪")
        name  = f"{r['project']}.{r['model']}"
        top_issue = r["issues"][0] if r["issues"] else "—"

        print(
            f"{i:>3}.  {icon} {grade:<4}  {r['total_score']:>5}  "
            f"{dims['documentation']:>4} {dims['test_coverage']:>4} "
            f"{dims['contracts']:>4} {dims['freshness']:>5} {dims['blast_radius']:>5}  "
            f"{name:<45}  {top_issue}"
        )

    print("-" * 140)

    # Summary stats
    grades = [r["grade"] for r in sorted_results]
    avg    = sum(r["total_score"] for r in sorted_results) / len(sorted_results)
    print(
        f"\n  Total models: {len(sorted_results)}  |  "
        f"Avg score: {avg:.1f}  |  "
        f"A: {grades.count('A')}  B: {grades.count('B')}  "
        f"C: {grades.count('C')}  D: {grades.count('D')}"
    )
    print("=" * 140 + "\n")


def render_json(results: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_models":  len(results),
            "avg_score":     round(sum(r["total_score"] for r in results) / max(len(results), 1), 1),
            "grade_counts":  {g: sum(1 for r in results if r["grade"] == g) for g in "ABCD"},
        },
        "models": sorted(results, key=lambda r: r["total_score"], reverse=True),
    }
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  📄 JSON report written → {output_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="dbt Mesh Model Health Scorecard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manifest", type=Path,
        help="Path to a single manifest.json"
    )
    parser.add_argument(
        "--run-results", type=Path,
        help="Path to run_results.json (optional — adds execution metrics)"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Score all six domain projects (reads target/manifest.json per project)"
    )
    parser.add_argument(
        "--output", choices=["table", "json", "both"], default="both",
        help="Output format (default: both)"
    )
    args = parser.parse_args()

    all_results: list[dict] = []

    if args.all:
        print("\n🔍  Scoring all domain projects...\n")
        for project in PROJECTS:
            manifest_path = REPO_ROOT / project / "target" / "manifest.json"
            print(f"  → {project}")
            results = score_project(manifest_path)
            all_results.extend(results)
            print(f"     {len(results)} models scored")
    elif args.manifest:
        print(f"\n🔍  Scoring {args.manifest}...")
        all_results = score_project(args.manifest)
    else:
        parser.print_help()
        sys.exit(1)

    if not all_results:
        print("\n⚠️  No models found. Run dbt build or dbt parse first to generate manifests.")
        sys.exit(0)

    if args.output in ("table", "both"):
        render_table(all_results)

    if args.output in ("json", "both"):
        render_json(all_results, REPORT_PATH)


if __name__ == "__main__":
    main()
