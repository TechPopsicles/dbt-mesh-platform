#!/usr/bin/env python3
"""
=============================================================================
dbt-mesh-platform  ·  Lineage Agent
=============================================================================
Reads dbt's manifest.json and surfaces four categories of DAG health risk
that are invisible during day-to-day development:

  Risk 1 — Orphaned sources     (defined but never referenced)
  Risk 2 — Untested models      (zero data_tests)
  Risk 3 — High blast radius    (many downstream dependents)
  Risk 4 — Missing descriptions (no model-level description)

No Snowflake connection. No API calls. Pure Python reading manifest.json.

Output:
  - Terminal report   → colour-coded, sorted by severity
  - lineage_report.md → committed to repo as living health document
  - lineage_report.json → machine-readable, consumed by scorecard (Post 5)

Prerequisites:
  cd dbt_platform && dbt parse   (generates target/manifest.json)

Usage:
  python agents/lineage_agent.py \
      --manifest    dbt_platform/target/manifest.json \
      --project     dbt_platform \
      --output-dir  agents/reports

Options:
  --blast-radius-threshold  INT   Models with >= N downstream nodes flagged (default: 3)
  --dry-run                       Print report without writing files
=============================================================================
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Optional rich output ───────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    console = Console()
    def log(msg, style=""):   console.print(msg, style=style)
    def ok(msg):              console.print(f"  ✅ {msg}", style="green")
    def warn(msg):            console.print(f"  ⚠️  {msg}", style="yellow")
    def err(msg):             console.print(f"  ❌ {msg}", style="red")
    def header(msg):          console.rule(f"[bold cyan]{msg}[/bold cyan]")
    HAS_RICH = True
except ImportError:
    def log(msg, style=""):   print(msg)
    def ok(msg):              print(f"  OK  {msg}")
    def warn(msg):            print(f"  WARN {msg}")
    def err(msg):             print(f"  ERR  {msg}")
    def header(msg):          print(f"\n{'='*60}\n{msg}\n{'='*60}")
    HAS_RICH = False


# =============================================================================
# MANIFEST LOADER
# =============================================================================

def load_manifest(manifest_path: Path) -> dict:
    """Load and validate manifest.json."""
    if not manifest_path.exists():
        err(f"manifest.json not found at {manifest_path}")
        err("Run: cd dbt_platform && dbt parse")
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    metadata = manifest.get("metadata", {})
    ok(f"Manifest loaded")
    ok(f"  dbt version  : {metadata.get('dbt_version', 'unknown')}")
    ok(f"  Generated at : {metadata.get('generated_at', 'unknown')}")
    ok(f"  Project      : {metadata.get('project_name', 'unknown')}")

    return manifest


# =============================================================================
# GRAPH BUILDERS
# =============================================================================

def extract_models(manifest: dict, project: str) -> dict[str, dict]:
    """
    Extract all model nodes for the given project.
    Returns dict of unique_id → node metadata.
    """
    models = {}
    for uid, node in manifest.get("nodes", {}).items():
        if (
            node.get("resource_type") == "model"
            and node.get("package_name") == project
        ):
            models[uid] = node
    return models


def extract_sources(manifest: dict, project: str) -> dict[str, dict]:
    """Extract all source nodes for the given project."""
    return {
        uid: node
        for uid, node in manifest.get("sources", {}).items()
        if node.get("package_name") == project
    }


def extract_tests(manifest: dict, project: str) -> dict[str, list[str]]:
    """
    Build a map of model_unique_id → list of test names attached to it.
    Handles both data_tests and unit_tests.
    """
    test_map: dict[str, list[str]] = defaultdict(list)

    for uid, node in manifest.get("nodes", {}).items():
        resource_type = node.get("resource_type", "")
        if resource_type not in ("test", "unit_test"):
            continue

        # Tests reference their parent model via depends_on
        depends = node.get("depends_on", {}).get("nodes", [])
        for parent_uid in depends:
            if parent_uid.startswith("model."):
                test_name = node.get("name", uid)
                test_map[parent_uid].append(test_name)

    return dict(test_map)


def build_downstream_map(manifest: dict, models: dict) -> dict[str, list[str]]:
    """
    Build downstream dependency map: model_uid → list of downstream model uids.
    Uses child_map from manifest, filtered to model→model edges only.
    """
    child_map = manifest.get("child_map", {})
    downstream: dict[str, list[str]] = {}

    for uid in models:
        children = child_map.get(uid, [])
        # Only count model children, not tests or analyses
        model_children = [c for c in children if c.startswith("model.")]
        downstream[uid] = model_children

    return downstream


def build_upstream_map(manifest: dict, models: dict) -> dict[str, list[str]]:
    """
    Build upstream dependency map: model_uid → list of upstream node uids.
    Includes both model and source parents.
    """
    parent_map = manifest.get("parent_map", {})
    upstream: dict[str, list[str]] = {}

    for uid in models:
        parents = parent_map.get(uid, [])
        upstream[uid] = parents

    return upstream


def find_referenced_sources(manifest: dict, project: str) -> set[str]:
    """Find all source unique_ids actually referenced by models."""
    referenced = set()
    parent_map = manifest.get("parent_map", {})

    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "model":
            continue
        parents = parent_map.get(uid, [])
        for parent in parents:
            if parent.startswith("source."):
                referenced.add(parent)

    return referenced


# =============================================================================
# RISK ANALYSERS
# =============================================================================

def analyse_orphaned_sources(
    sources: dict,
    referenced_sources: set,
) -> list[dict]:
    """Risk 1: Sources defined but never referenced by any model."""
    orphans = []
    for uid, source in sources.items():
        if uid not in referenced_sources:
            orphans.append({
                "uid":         uid,
                "source_name": source.get("source_name", ""),
                "name":        source.get("name", ""),
                "path":        source.get("original_file_path", ""),
                "risk":        "orphaned_source",
                "severity":    "warn",
                "message":     f"Source '{source.get('source_name')}.{source.get('name')}' "
                               f"is defined but never referenced by any model.",
            })
    return orphans


def analyse_untested_models(
    models: dict,
    test_map: dict,
) -> list[dict]:
    """Risk 2: Models with zero data_tests."""
    untested = []
    for uid, model in models.items():
        tests = test_map.get(uid, [])
        if not tests:
            untested.append({
                "uid":      uid,
                "name":     model.get("name", ""),
                "path":     model.get("original_file_path", ""),
                "risk":     "untested_model",
                "severity": "error",
                "message":  f"Model '{model.get('name')}' has zero data_tests. "
                            f"Add at minimum not_null + unique on the primary key.",
            })
    return untested


def analyse_blast_radius(
    models: dict,
    downstream: dict,
    threshold: int,
) -> list[dict]:
    """Risk 3: Models with >= threshold downstream dependents."""
    high_blast = []
    for uid, model in models.items():
        children = downstream.get(uid, [])
        if len(children) >= threshold:
            # Calculate full transitive downstream count
            visited  = set()
            queue    = list(children)
            while queue:
                node = queue.pop()
                if node not in visited:
                    visited.add(node)
                    queue.extend(downstream.get(node, []))

            high_blast.append({
                "uid":               uid,
                "name":              model.get("name", ""),
                "path":              model.get("original_file_path", ""),
                "direct_children":   len(children),
                "transitive_count":  len(visited),
                "child_names":       [
                    models[c].get("name", c)
                    for c in children
                    if c in models
                ],
                "risk":     "high_blast_radius",
                "severity": "warn",
                "message":  f"Model '{model.get('name')}' has {len(children)} direct "
                            f"and {len(visited)} transitive downstream dependents. "
                            f"Breakage here impacts {len(visited)} models. "
                            f"Ensure contract: enforced and strict data_tests.",
            })

    return sorted(high_blast, key=lambda x: x["transitive_count"], reverse=True)


def analyse_missing_descriptions(
    models: dict,
) -> list[dict]:
    """Risk 4: Models with no or thin model-level description."""
    missing = []
    for uid, model in models.items():
        desc = model.get("description", "").strip()
        word_count = len(desc.split()) if desc else 0
        if word_count < 10:
            missing.append({
                "uid":        uid,
                "name":       model.get("name", ""),
                "path":       model.get("original_file_path", ""),
                "word_count": word_count,
                "risk":       "missing_description",
                "severity":   "warn",
                "message":    f"Model '{model.get('name')}' has a thin model-level "
                              f"description ({word_count} words). "
                              f"Add a business-context description for the catalog.",
            })
    return missing


# =============================================================================
# REPORTERS
# =============================================================================

def print_terminal_report(
    models:         dict,
    test_map:       dict,
    downstream:     dict,
    orphans:        list,
    untested:       list,
    blast_radius:   list,
    missing_desc:   list,
    threshold:      int,
) -> None:
    """Print colour-coded DAG health report to terminal."""

    header("DAG Health Summary")

    total_models   = len(models)
    tested_models  = sum(1 for uid in models if test_map.get(uid))
    test_coverage  = (tested_models / total_models * 100) if total_models else 0

    log(f"\n  Total models     : {total_models}")
    log(f"  Tested models    : {tested_models}  ({test_coverage:.0f}% coverage)")
    log(f"  Total data_tests : {sum(len(t) for t in test_map.values())}")
    log("")

    # ── Risk 1: Orphaned sources ───────────────────────────────────────────────
    header(f"Risk 1 · Orphaned Sources  ({len(orphans)} found)")
    if orphans:
        for o in orphans:
            warn(f"  {o['source_name']}.{o['name']}")
            log(f"    {o['message']}")
    else:
        ok("No orphaned sources — all sources are referenced")

    # ── Risk 2: Untested models ────────────────────────────────────────────────
    header(f"Risk 2 · Untested Models  ({len(untested)} found)")
    if untested:
        for u in untested:
            err(f"  {u['name']}")
            log(f"    {u['message']}")
    else:
        ok("No untested models — all models have data_tests")

    # ── Risk 3: High blast radius ──────────────────────────────────────────────
    header(f"Risk 3 · High Blast Radius  (threshold: {threshold} | {len(blast_radius)} found)")
    if blast_radius:
        for b in blast_radius:
            warn(f"  {b['name']}  →  {b['direct_children']} direct  ·  {b['transitive_count']} transitive")
            if b["child_names"]:
                log(f"    Downstream: {', '.join(b['child_names'][:5])}"
                    + (" ..." if len(b["child_names"]) > 5 else ""))
            log(f"    {b['message']}")
    else:
        ok(f"No models exceed blast radius threshold of {threshold}")

    # ── Risk 4: Missing descriptions ───────────────────────────────────────────
    header(f"Risk 4 · Thin Model Descriptions  ({len(missing_desc)} found)")
    if missing_desc:
        for m in missing_desc:
            warn(f"  {m['name']}  ({m['word_count']} words)")
            log(f"    {m['message']}")
    else:
        ok("All models have adequate model-level descriptions")

    # ── Overall health score ───────────────────────────────────────────────────
    header("Overall DAG Health Score")

    error_count = len(untested)
    warn_count  = len(orphans) + len(blast_radius) + len(missing_desc)

    if error_count == 0 and warn_count == 0:
        ok("🟢  HEALTHY — no risks detected")
    elif error_count == 0:
        warn(f"🟡  NEEDS ATTENTION — {warn_count} warnings, 0 errors")
    else:
        err(f"🔴  AT RISK — {error_count} errors, {warn_count} warnings")

    log("")


def write_markdown_report(
    output_dir:   Path,
    project:      str,
    models:       dict,
    test_map:     dict,
    downstream:   dict,
    orphans:      list,
    untested:     list,
    blast_radius: list,
    missing_desc: list,
    threshold:    int,
) -> Path:
    """Write lineage_report.md — living health document committed to the repo."""

    now         = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total       = len(models)
    tested      = sum(1 for uid in models if test_map.get(uid))
    coverage    = (tested / total * 100) if total else 0
    total_tests = sum(len(t) for t in test_map.values())

    error_count = len(untested)
    warn_count  = len(orphans) + len(blast_radius) + len(missing_desc)

    if error_count == 0 and warn_count == 0:
        health_badge = "🟢 HEALTHY"
    elif error_count == 0:
        health_badge = "🟡 NEEDS ATTENTION"
    else:
        health_badge = "🔴 AT RISK"

    lines = [
        f"# dbt Lineage Health Report — {project}",
        f"",
        f"**Generated:** {now}  ",
        f"**Overall health:** {health_badge}  ",
        f"**Models:** {total} total · {tested} tested ({coverage:.0f}% coverage)  ",
        f"**Data tests:** {total_tests} total  ",
        f"**Errors:** {error_count} · **Warnings:** {warn_count}",
        f"",
        f"---",
        f"",
        f"## Risk 1 — Orphaned Sources ({len(orphans)})",
        f"",
        f"Sources defined in YAML but never referenced by any model.",
        f"",
    ]

    if orphans:
        for o in orphans:
            lines += [
                f"- ⚠️  **{o['source_name']}.{o['name']}**",
                f"  - {o['message']}",
                f"  - File: `{o['path']}`",
            ]
    else:
        lines.append("✅ No orphaned sources found.")

    lines += [
        f"",
        f"---",
        f"",
        f"## Risk 2 — Untested Models ({len(untested)})",
        f"",
        f"Models with zero data_tests — no assertions on output quality.",
        f"",
    ]

    if untested:
        for u in untested:
            lines += [
                f"- ❌ **{u['name']}**",
                f"  - {u['message']}",
                f"  - File: `{u['path']}`",
            ]
    else:
        lines.append("✅ All models have at least one data_test.")

    lines += [
        f"",
        f"---",
        f"",
        f"## Risk 3 — High Blast Radius (threshold: {threshold})",
        f"",
        f"Models with {threshold}+ downstream dependents.",
        f"Breakage here cascades across many models.",
        f"",
    ]

    if blast_radius:
        for b in blast_radius:
            children_str = ", ".join(f"`{c}`" for c in b["child_names"][:5])
            if len(b["child_names"]) > 5:
                children_str += f" +{len(b['child_names'])-5} more"
            lines += [
                f"- ⚠️  **{b['name']}**  "
                f"({b['direct_children']} direct · {b['transitive_count']} transitive downstream)",
                f"  - {b['message']}",
                f"  - Downstream: {children_str}",
            ]
    else:
        lines.append(f"✅ No models exceed blast radius threshold of {threshold}.")

    lines += [
        f"",
        f"---",
        f"",
        f"## Risk 4 — Thin Model Descriptions ({len(missing_desc)})",
        f"",
        f"Models with fewer than 10 words in their model-level description.",
        f"",
    ]

    if missing_desc:
        for m in missing_desc:
            lines += [
                f"- ⚠️  **{m['name']}** ({m['word_count']} words)",
                f"  - {m['message']}",
            ]
    else:
        lines.append("✅ All models have adequate model-level descriptions.")

    lines += [
        f"",
        f"---",
        f"",
        f"## Model Test Coverage",
        f"",
        f"| Model | Tests | Blast Radius |",
        f"|---|---|---|",
    ]

    for uid, model in sorted(models.items(), key=lambda x: x[1].get("name", "")):
        name       = model.get("name", "")
        tests      = test_map.get(uid, [])
        n_tests    = len(tests)
        n_children = len(downstream.get(uid, []))
        blast_flag = f"⚠️ {n_children}" if n_children >= threshold else str(n_children)
        test_flag  = f"❌ 0" if n_tests == 0 else str(n_tests)
        lines.append(f"| `{name}` | {test_flag} | {blast_flag} |")

    lines += [
        f"",
        f"---",
        f"",
        f"*Generated by `agents/lineage_agent.py` · dbt-mesh-platform*",
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "lineage_report.md"
    md_path.write_text("\n".join(lines))
    return md_path


def write_json_report(
    output_dir:   Path,
    project:      str,
    models:       dict,
    test_map:     dict,
    downstream:   dict,
    orphans:      list,
    untested:     list,
    blast_radius: list,
    missing_desc: list,
) -> Path:
    """Write lineage_report.json — machine-readable input for scorecard (Post 5)."""

    report = {
        "project":        project,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_models":   len(models),
            "tested_models":  sum(1 for uid in models if test_map.get(uid)),
            "total_tests":    sum(len(t) for t in test_map.values()),
            "error_count":    len(untested),
            "warn_count":     len(orphans) + len(blast_radius) + len(missing_desc),
        },
        "risks": {
            "orphaned_sources":    orphans,
            "untested_models":     untested,
            "high_blast_radius":   blast_radius,
            "missing_descriptions": missing_desc,
        },
        "models": {
            uid: {
                "name":            model.get("name"),
                "path":            model.get("original_file_path"),
                "description":     model.get("description", ""),
                "test_count":      len(test_map.get(uid, [])),
                "downstream_count": len(downstream.get(uid, [])),
            }
            for uid, model in models.items()
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "lineage_report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    return json_path


# =============================================================================
# MAIN
# =============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analyse dbt manifest.json for DAG health risks"
    )
    p.add_argument("--manifest",              required=True,
                   help="Path to manifest.json  e.g. dbt_platform/target/manifest.json")
    p.add_argument("--project",               required=True,
                   help="dbt project name  e.g. dbt_platform")
    p.add_argument("--output-dir",            default="agents/reports",
                   help="Directory for report files (default: agents/reports)")
    p.add_argument("--blast-radius-threshold", type=int, default=3,
                   help="Flag models with >= N downstream dependents (default: 3)")
    p.add_argument("--dry-run",               action="store_true",
                   help="Print report without writing output files")
    return p.parse_args()


def main() -> None:
    args          = parse_args()
    manifest_path = Path(args.manifest)
    output_dir    = Path(args.output_dir)

    header("dbt Lineage Agent")
    log(f"  manifest  : {manifest_path}")
    log(f"  project   : {args.project}")
    log(f"  output    : {output_dir}")
    log(f"  threshold : {args.blast_radius_threshold} downstream dependents")
    log(f"  dry-run   : {args.dry_run}\n")

    # ── Load manifest ──────────────────────────────────────────────────────────
    header("1 · Loading manifest.json")
    manifest = load_manifest(manifest_path)

    # ── Extract graph nodes ────────────────────────────────────────────────────
    header("2 · Extracting DAG nodes")
    models  = extract_models(manifest, args.project)
    sources = extract_sources(manifest, args.project)
    ok(f"Models  : {len(models)}")
    ok(f"Sources : {len(sources)}")

    # ── Build dependency maps ──────────────────────────────────────────────────
    header("3 · Building dependency graph")
    test_map            = extract_tests(manifest, args.project)
    downstream          = build_downstream_map(manifest, models)
    upstream            = build_upstream_map(manifest, models)
    referenced_sources  = find_referenced_sources(manifest, args.project)

    total_tests = sum(len(t) for t in test_map.values())
    ok(f"Tests indexed      : {total_tests}")
    ok(f"Sources referenced : {len(referenced_sources)}")

    # ── Run risk analyses ──────────────────────────────────────────────────────
    header("4 · Analysing risks")
    orphans      = analyse_orphaned_sources(sources, referenced_sources)
    untested     = analyse_untested_models(models, test_map)
    blast_radius = analyse_blast_radius(models, downstream, args.blast_radius_threshold)
    missing_desc = analyse_missing_descriptions(models)

    ok(f"Risk 1 — Orphaned sources     : {len(orphans)}")
    ok(f"Risk 2 — Untested models      : {len(untested)}")
    ok(f"Risk 3 — High blast radius    : {len(blast_radius)}")
    ok(f"Risk 4 — Missing descriptions : {len(missing_desc)}")

    # ── Print terminal report ──────────────────────────────────────────────────
    print_terminal_report(
        models, test_map, downstream,
        orphans, untested, blast_radius, missing_desc,
        args.blast_radius_threshold,
    )

    if args.dry_run:
        warn("Dry run — no files written")
        return

    # ── Write report files ─────────────────────────────────────────────────────
    header("5 · Writing reports")
    md_path   = write_markdown_report(
        output_dir, args.project, models, test_map, downstream,
        orphans, untested, blast_radius, missing_desc,
        args.blast_radius_threshold,
    )
    json_path = write_json_report(
        output_dir, args.project, models, test_map, downstream,
        orphans, untested, blast_radius, missing_desc,
    )

    ok(f"Markdown  → {md_path}")
    ok(f"JSON      → {json_path}")

    log("\n  Next steps:", style="bold")
    log("    1. Review lineage_report.md — fix errors before warnings")
    log("    2. Add data_tests to any untested models (Risk 2 — severity: error)")
    log("    3. Add contract: enforced to high blast-radius models (Risk 3)")
    log("    4. Run constraint_agent.py to generate contracts + freshness thresholds")
    log("    5. Commit reports: git add agents/reports/ && git commit -m 'feat: lineage report'\n")


if __name__ == "__main__":
    main()
