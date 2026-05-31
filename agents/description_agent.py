#!/usr/bin/env python3
"""
=============================================================================
dbt-mesh-platform  ·  Description Agent
=============================================================================
Reads dbt staging YAML files, calls the Anthropic API with full model
context (SQL + column names + data types + sibling columns), and rewrites
every column description with business-focused, catalog-ready prose.

Design decisions:
  - One API call per model (not per column) — cheaper, more coherent
  - Overwrites existing descriptions — Git is the audit trail
  - Reads staging SQL for context — agent sees the actual transformations
  - Returns JSON from API — clean parse, no string manipulation
  - Dry-run mode — preview changes without writing files

Usage:
  python agents/description_agent.py \
      --project-dir  dbt_platform \
      --models-dir   dbt_platform/models/staging/tpch \
      --source       tpch

Environment variables required:
  ANTHROPIC_API_KEY          — your Anthropic API key
  SNOWFLAKE_ACCOUNT          — used for context only (not queried here)

Install:
  pip install anthropic pyyaml rich
=============================================================================
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# ── Optional rich output ───────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    console = Console()
    def log(msg, style=""):       console.print(msg, style=style)
    def ok(msg):                  console.print(f"  ✅ {msg}", style="green")
    def warn(msg):                console.print(f"  ⚠️  {msg}", style="yellow")
    def err(msg):                 console.print(f"  ❌ {msg}", style="red")
    def header(msg):              console.rule(f"[bold cyan]{msg}[/bold cyan]")
    def panel(title, content):    console.print(Panel(content, title=title))
    HAS_RICH = True
except ImportError:
    def log(msg, style=""):       print(msg)
    def ok(msg):                  print(f"  OK  {msg}")
    def warn(msg):                print(f"  WARN {msg}")
    def err(msg):                 print(f"  ERR  {msg}")
    def header(msg):              print(f"\n{'='*60}\n{msg}\n{'='*60}")
    def panel(title, content):    print(f"\n[{title}]\n{content}")
    HAS_RICH = False


# =============================================================================
# ANTHROPIC CLIENT
# =============================================================================

def get_anthropic_client():
    """Initialise Anthropic client from environment variable."""
    try:
        import anthropic
    except ImportError:
        err("anthropic package not installed.")
        err("Run: pip install anthropic")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        err("ANTHROPIC_API_KEY environment variable not set.")
        err("Get your key at: https://console.anthropic.com")
        sys.exit(1)

    ok("Anthropic client initialised")
    return anthropic.Anthropic(api_key=api_key)


# =============================================================================
# FILE READERS
# =============================================================================

def find_yaml_files(models_dir: Path, source: str) -> list[Path]:
    """Find all stg_{source}.yml schema files in the models directory."""
    pattern = f"stg_{source}*.yml"
    files = list(models_dir.glob(pattern))
    # Also look recursively
    if not files:
        files = list(models_dir.rglob(pattern))
    return sorted(files)


def find_sql_file(models_dir: Path, model_name: str) -> Path | None:
    """Find the SQL file for a given model name."""
    candidates = list(models_dir.rglob(f"{model_name}.sql"))
    return candidates[0] if candidates else None


def read_sql(sql_path: Path) -> str:
    """Read staging SQL file, strip comments for cleaner context."""
    content = sql_path.read_text()
    # Remove single-line comments but keep the SQL structure
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def load_yaml(yaml_path: Path) -> dict:
    """Load a YAML file preserving structure."""
    return yaml.safe_load(yaml_path.read_text()) or {}


# =============================================================================
# PROMPT BUILDER
# =============================================================================

def build_prompt(
    model_name: str,
    sql_content: str,
    columns: list[dict],
    source_name: str,
) -> str:
    """
    Build a rich context prompt for the Anthropic API.
    One prompt per model — includes sibling columns for coherent descriptions.
    """
    # Format column list for the prompt
    col_lines = []
    for col in columns:
        name        = col.get("name", "")
        dtype       = col.get("data_type", "unknown")
        current_desc = col.get("description", "")
        tests       = col.get("tests", [])
        test_names  = [
            t if isinstance(t, str) else list(t.keys())[0]
            for t in tests
        ]
        col_lines.append(
            f"  - {name} ({dtype})"
            + (f" | tests: {', '.join(test_names)}" if test_names else "")
            + (f" | current: {current_desc}" if current_desc else "")
        )

    col_block = "\n".join(col_lines)

    prompt = f"""You are a senior analytics engineer writing dbt column descriptions for a production data catalog.

Your descriptions will be read by business analysts, data scientists, and other engineers.
They must be clear, precise, and business-focused — not just restating the column name.

## Context

Model name: {model_name}
Source system: {source_name.upper()} (Snowflake Sample Data — TPC-H supply chain benchmark)
This is a staging model: it renames, casts, and lightly transforms raw source columns.

## Staging SQL

```sql
{sql_content}
```

## Columns to describe

{col_block}

## Instructions

Write a description for EVERY column listed above. For each description:
- 1 to 2 sentences maximum
- Start with what the column IS (the business concept), not what it's called
- If it's a primary key: say "Surrogate primary key for [entity]."
- If it's a foreign key: say "Foreign key to [related entity]."
- If it's a date/timestamp: mention what event it captures
- If it's a monetary amount: mention the currency context if inferable
- If it's a status/flag: describe what the values represent
- If it's a derived column (visible in the SQL): explain the derivation briefly
- If it's a PII field (name, address, phone, email): end with "PII — mask in public marts."
- Do NOT use phrases like "This column contains" or "This field represents"
- Do NOT just repeat the column name in different words

## Response format

Return ONLY a valid JSON object. No markdown. No explanation. No code fences.
Keys are exact column names as listed above. Values are the description strings.

Example format:
{{"order_key": "Surrogate primary key for the order.", "customer_key": "Foreign key to the customer who placed this order."}}"""

    return prompt


# =============================================================================
# API CALLER
# =============================================================================

def call_anthropic(
    client,
    prompt: str,
    model_name: str,
    max_retries: int = 3,
) -> dict[str, str]:
    """
    Call Anthropic API and parse JSON response.
    Returns dict of column_name -> description.
    """
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()

            descriptions = json.loads(raw)

            if not isinstance(descriptions, dict):
                raise ValueError("Response is not a JSON object")

            return descriptions

        except json.JSONDecodeError as e:
            warn(f"  JSON parse failed (attempt {attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                err(f"  Failed to parse API response for {model_name} after {max_retries} attempts")
                return {}
            time.sleep(2 ** attempt)  # exponential backoff

        except Exception as e:
            warn(f"  API call failed (attempt {attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                err(f"  API call failed for {model_name} after {max_retries} attempts")
                return {}
            time.sleep(2 ** attempt)

    return {}


# =============================================================================
# YAML UPDATER
# =============================================================================

def update_model_descriptions(
    model: dict,
    descriptions: dict[str, str],
    models_dir: Path,
) -> tuple[dict, int, int]:
    """
    Update column descriptions in a model dict.
    Returns (updated_model, updated_count, skipped_count).
    """
    updated   = 0
    skipped   = 0
    columns   = model.get("columns", [])

    for col in columns:
        col_name = col.get("name", "")
        new_desc = descriptions.get(col_name, "")

        if not new_desc:
            # Try snake_case match if exact match fails
            snake = col_name.lower().replace(" ", "_")
            new_desc = descriptions.get(snake, "")

        if new_desc:
            old_desc = col.get("description", "")
            col["description"] = new_desc
            if old_desc and old_desc != new_desc:
                updated += 1
            elif not old_desc:
                updated += 1
        else:
            skipped += 1
            warn(f"    No description returned for column: {col_name}")

    model["columns"] = columns
    return model, updated, skipped


def write_yaml(data: dict, path: Path) -> None:
    """Write YAML with clean formatting — no aliases, readable indentation."""
    class CleanDumper(yaml.Dumper):
        def ignore_aliases(self, data):
            return True

    content = yaml.dump(
        data,
        Dumper=CleanDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        indent=2,
        width=120,
    )
    path.write_text(content)


# =============================================================================
# COVERAGE REPORTER
# =============================================================================

def report_coverage(
    model_name: str,
    columns: list[dict],
    descriptions: dict[str, str],
) -> None:
    """Print a before/after coverage summary for one model."""

    before_empty = sum(
        1 for col in columns
        if not col.get("description", "").strip()
        or len(col.get("description", "").split()) < 5
    )
    after_empty = sum(
        1 for col in columns
        if col["name"] not in descriptions
    )
    total = len(columns)

    ok(f"  {model_name}")
    log(f"    Columns       : {total}")
    log(f"    Enriched      : {len(descriptions)}")
    log(f"    Thin before   : {before_empty}")
    log(f"    Missing after : {after_empty}")

    if descriptions:
        # Show a sample description
        sample_col  = next(iter(descriptions))
        sample_desc = descriptions[sample_col]
        log(f"    Sample        : [{sample_col}] {sample_desc[:80]}{'...' if len(sample_desc) > 80 else ''}")


# =============================================================================
# MAIN
# =============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Enrich dbt staging YAML descriptions using the Anthropic API"
    )
    p.add_argument("--project-dir",  required=True,
                   help="dbt project root  e.g. dbt_platform")
    p.add_argument("--models-dir",   required=True,
                   help="Path to staging models  e.g. dbt_platform/models/staging/tpch")
    p.add_argument("--source",       required=True,
                   help="Source name  e.g. tpch")
    p.add_argument("--model",        default=None,
                   help="Run on a single model only  e.g. stg_tpch__orders")
    p.add_argument("--dry-run",      action="store_true",
                   help="Print enriched descriptions without writing files")
    p.add_argument("--delay",        type=float, default=1.0,
                   help="Seconds to wait between API calls (default: 1.0)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    models_dir  = Path(args.models_dir)
    project_dir = Path(args.project_dir)

    header("dbt Description Agent")
    log(f"  project-dir : {project_dir}")
    log(f"  models-dir  : {models_dir}")
    log(f"  source      : {args.source}")
    log(f"  model       : {args.model or 'all'}")
    log(f"  dry-run     : {args.dry_run}\n")

    # ── Connect to Anthropic ───────────────────────────────────────────────────
    header("1 · Connecting to Anthropic")
    client = get_anthropic_client()

    # ── Find YAML schema files ─────────────────────────────────────────────────
    header("2 · Finding staging YAML files")
    yaml_files = find_yaml_files(models_dir, args.source)

    if not yaml_files:
        err(f"No stg_{args.source}*.yml files found in {models_dir}")
        sys.exit(1)

    ok(f"Found {len(yaml_files)} YAML file(s)")
    for f in yaml_files:
        log(f"    {f}")

    # ── Process each YAML file ─────────────────────────────────────────────────
    header("3 · Enriching descriptions")

    total_models   = 0
    total_updated  = 0
    total_skipped  = 0
    total_api_calls = 0

    for yaml_path in yaml_files:
        data = load_yaml(yaml_path)
        models = data.get("models", [])

        if not models:
            warn(f"No models found in {yaml_path.name}")
            continue

        models_updated = False

        for i, model in enumerate(models):
            model_name = model.get("name", "")

            # Filter to specific model if --model flag set
            if args.model and model_name != args.model:
                continue

            columns = model.get("columns", [])
            if not columns:
                warn(f"  {model_name} — no columns defined, skipping")
                continue

            log(f"\n  📋 {model_name} ({len(columns)} columns)")

            # Find and read the staging SQL
            sql_path = find_sql_file(models_dir, model_name)
            if sql_path:
                sql_content = read_sql(sql_path)
                ok(f"  SQL loaded from {sql_path.name}")
            else:
                warn(f"  SQL file not found for {model_name} — proceeding without SQL context")
                sql_content = f"-- SQL file not found for {model_name}"

            # Build prompt
            prompt = build_prompt(
                model_name  = model_name,
                sql_content = sql_content,
                columns     = columns,
                source_name = args.source,
            )

            if args.dry_run:
                log(f"\n  [DRY RUN] Prompt for {model_name}:")
                log("  " + "─" * 60)
                log(prompt[:800] + "..." if len(prompt) > 800 else prompt)
                log("  " + "─" * 60)
                continue

            # Call Anthropic API
            log(f"  🤖 Calling claude-sonnet-4-6...")
            descriptions = call_anthropic(client, prompt, model_name)
            total_api_calls += 1

            if not descriptions:
                warn(f"  No descriptions returned for {model_name}")
                continue

            # Update model
            updated_model, n_updated, n_skipped = update_model_descriptions(
                model, descriptions, models_dir
            )
            models[i]      = updated_model
            total_updated  += n_updated
            total_skipped  += n_skipped
            total_models   += 1
            models_updated  = True

            # Coverage report
            report_coverage(model_name, columns, descriptions)

            # Rate limit pause between models
            if args.delay > 0 and i < len(models) - 1:
                time.sleep(args.delay)

        # Write updated YAML back to disk
        if models_updated and not args.dry_run:
            data["models"] = models
            write_yaml(data, yaml_path)
            ok(f"\n  Written → {yaml_path}")

    # ── Final summary ──────────────────────────────────────────────────────────
    header("Done")
    ok(f"Models processed  : {total_models}")
    ok(f"Columns enriched  : {total_updated}")
    ok(f"Columns skipped   : {total_skipped}")
    ok(f"API calls made    : {total_api_calls}")

    log("\n  Next steps:", style="bold")
    log("    1. Review enriched descriptions in your YAML files")
    log("    2. Commit: git add models/ && git commit -m 'feat: AI-enriched column descriptions'")
    log("    3. Run dbt docs generate && dbt docs serve — see the catalog")
    log("    4. Run test_agent.py to profile real values and add accepted_values tests\n")


if __name__ == "__main__":
    main()
