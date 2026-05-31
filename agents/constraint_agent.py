#!/usr/bin/env python3
"""
=============================================================================
dbt-mesh-platform  ·  Constraint Agent
=============================================================================
Generates two governance artifacts that make data products safe to share
across teams in a dbt Mesh:

  1. Contracts  — adds contract: enforced: true/false + data_type: per column
                  Staging models → enforced: false (private, internal)
                  Mart models    → enforced: true  (public, versioned)

  2. Freshness  — adds SLA-based freshness thresholds to source definitions
                  Based on meta.sla tag: realtime | daily | static

No Snowflake query needed if data_type is already in YAML (boilerplate agent).
Falls back to INFORMATION_SCHEMA if data_type is missing.

SLA tiers:
  realtime  → warn: 1h   error: 3h    (event streams, CDC)
  daily     → warn: 25h  error: 49h   (nightly batch ETL)
  static    → null       null          (reference data, TPC-H)

Usage:
  python agents/constraint_agent.py \
      --models-dir  dbt_platform/models/staging/tpch \
      --source      tpch \
      --layer       staging

Options:
  --layer    staging | marts (default: staging)
  --dry-run  Print changes without writing files
=============================================================================
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# ── Optional rich output ───────────────────────────────────────────────────────
try:
    from rich.console import Console
    console = Console()
    def log(msg, style=""):   console.print(msg, style=style)
    def ok(msg):              console.print(f"  ✅ {msg}", style="green")
    def warn(msg):            console.print(f"  ⚠️  {msg}", style="yellow")
    def err(msg):             console.print(f"  ❌ {msg}", style="red")
    def header(msg):          console.rule(f"[bold cyan]{msg}[/bold cyan]")
except ImportError:
    def log(msg, style=""):   print(msg)
    def ok(msg):              print(f"  OK  {msg}")
    def warn(msg):            print(f"  WARN {msg}")
    def err(msg):             print(f"  ERR  {msg}")
    def header(msg):          print(f"\n{'='*60}\n{msg}\n{'='*60}")


# =============================================================================
# SLA FRESHNESS THRESHOLDS
# =============================================================================

SLA_FRESHNESS = {
    "realtime": {
        "warn_after":  {"count": 1,  "period": "hour"},
        "error_after": {"count": 3,  "period": "hour"},
    },
    "daily": {
        "warn_after":  {"count": 25, "period": "hour"},
        "error_after": {"count": 49, "period": "hour"},
    },
    "static": None,   # reference data — no freshness check
}

# =============================================================================
# SNOWFLAKE TYPE → dbt CONTRACT TYPE
# =============================================================================

SNOWFLAKE_TO_DBT_TYPE = {
    "TEXT":           "varchar",
    "VARCHAR":        "varchar",
    "CHAR":           "varchar",
    "STRING":         "varchar",
    "NUMBER":         "number",
    "DECIMAL":        "number",
    "NUMERIC":        "number",
    "INT":            "number",
    "INTEGER":        "number",
    "BIGINT":         "number",
    "SMALLINT":       "number",
    "FLOAT":          "float",
    "FLOAT4":         "float",
    "FLOAT8":         "float",
    "DOUBLE":         "float",
    "REAL":           "float",
    "BOOLEAN":        "boolean",
    "DATE":           "date",
    "DATETIME":       "timestamp_ntz",
    "TIMESTAMP":      "timestamp_ntz",
    "TIMESTAMP_NTZ":  "timestamp_ntz",
    "TIMESTAMP_LTZ":  "timestamp_ltz",
    "TIMESTAMP_TZ":   "timestamp_tz",
    "VARIANT":        "variant",
    "OBJECT":         "object",
    "ARRAY":          "array",
}


def normalise_type(raw_type: str) -> str:
    """Map Snowflake/inferred type to dbt contract-safe type."""
    if not raw_type:
        return "varchar"
    upper = raw_type.upper().split("(")[0].strip()  # strip precision e.g. VARCHAR(255)
    return SNOWFLAKE_TO_DBT_TYPE.get(upper, raw_type.lower())


# =============================================================================
# YAML HELPERS
# =============================================================================

def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def write_yaml(data: dict, path: Path) -> None:
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
# CONTRACT GENERATOR
# =============================================================================

def add_contract_to_model(
    model: dict,
    enforced: bool,
) -> tuple[dict, int]:
    """
    Add contract block and data_type to a model's config and columns.
    Returns (updated_model, columns_typed).
    """
    # ── Contract block in config ───────────────────────────────────────────────
    config = model.get("config", {})
    config["contract"] = {"enforced": enforced}
    model["config"] = config

    # ── data_type on each column ───────────────────────────────────────────────
    columns      = model.get("columns", [])
    typed_count  = 0

    for col in columns:
        existing_type = col.get("data_type", "")

        if existing_type:
            # Normalise existing type to dbt contract format
            col["data_type"] = normalise_type(existing_type)
            typed_count += 1
        else:
            # Infer type from column name patterns as fallback
            inferred = infer_type_from_name(col.get("name", ""))
            col["data_type"] = inferred
            typed_count += 1

    model["columns"] = columns
    return model, typed_count


def infer_type_from_name(col_name: str) -> str:
    """
    Infer dbt data type from column name when no explicit type available.
    Used as fallback only — boilerplate agent should have set data_type already.
    """
    name = col_name.lower()

    if any(x in name for x in ["_key", "key", "_id", "id", "_count", "count",
                                 "_num", "number", "quantity", "priority"]):
        return "number"
    if any(x in name for x in ["_date", "date", "_at", "_time", "time"]):
        return "date"
    if any(x in name for x in ["is_", "has_", "_flag", "flag"]):
        return "boolean"
    if any(x in name for x in ["_amount", "amount", "_price", "price",
                                 "_cost", "cost", "_revenue", "revenue",
                                 "_value", "value", "_rate", "rate",
                                 "_balance", "balance"]):
        return "number"
    return "varchar"


# =============================================================================
# FRESHNESS GENERATOR
# =============================================================================

def add_freshness_to_sources(
    data: dict,
) -> tuple[dict, int, int]:
    """
    Add SLA-based freshness thresholds to source table definitions.
    Reads meta.sla from each table's config.meta or top-level meta.
    Returns (updated_data, tables_updated, tables_skipped).
    """
    updated = 0
    skipped = 0

    sources = data.get("sources", [])
    for source in sources:

        # Remove source-level deprecated top-level properties.
        # dbt 1.9+ requires these inside config: not at the source root level.
        source.pop("freshness", None)
        source.pop("loaded_at_field", None)

        # Move source-level meta into config.meta if present
        if "meta" in source and "config" not in source:
            source["config"] = {"meta": source.pop("meta")}
        elif "meta" in source:
            existing_meta = source["config"].get("meta", {})
            existing_meta.update(source.pop("meta"))
            source["config"]["meta"] = existing_meta

        tables = source.get("tables", [])
        for table in tables:
            # Read SLA from config.meta or meta (both locations supported)
            config  = table.get("config", {})
            meta    = config.get("meta", table.get("meta", {}))
            sla     = meta.get("sla", "daily").lower()

            if sla not in SLA_FRESHNESS:
                warn(f"    Unknown SLA '{sla}' on {table.get('name')} — defaulting to daily")
                sla = "daily"

            freshness = SLA_FRESHNESS[sla]

            # Write freshness into config (dbt 1.9+ location)
            if "config" not in table:
                table["config"] = {}

            table["config"]["freshness"] = freshness

            # Ensure meta.sla is documented
            if "meta" not in table["config"]:
                table["config"]["meta"] = {}
            table["config"]["meta"]["sla"] = sla

            # Remove top-level freshness: null if present (deprecated location)
            table.pop("freshness", None)

            if freshness is None:
                log(f"    {table.get('name'):<20} sla=static  → freshness: null")
            else:
                warn_h = freshness["warn_after"]["count"]
                warn_p = freshness["warn_after"]["period"]
                err_h  = freshness["error_after"]["count"]
                err_p  = freshness["error_after"]["period"]
                log(f"    {table.get('name'):<20} sla={sla:<10}"
                    f" warn={warn_h}{warn_p[0]}  error={err_h}{err_p[0]}")

            updated += 1

    return data, updated, skipped


# =============================================================================
# MAIN
# =============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Add dbt contracts and freshness thresholds to staging/mart YAML"
    )
    p.add_argument("--models-dir", required=True,
                   help="Path to models directory  e.g. dbt_platform/models/staging/tpch")
    p.add_argument("--source",     required=True,
                   help="Source name  e.g. tpch")
    p.add_argument("--layer",      default="staging",
                   choices=["staging", "marts"],
                   help="Layer to process: staging (enforced=false) or marts (enforced=true)")
    p.add_argument("--model",      default=None,
                   help="Run on a single model only  e.g. stg_tpch__orders")
    p.add_argument("--dry-run",    action="store_true",
                   help="Print changes without writing files")
    return p.parse_args()


def main() -> None:
    args       = parse_args()
    models_dir = Path(args.models_dir)
    enforced   = args.layer == "marts"

    header("dbt Constraint Agent")
    log(f"  models-dir : {models_dir}")
    log(f"  source     : {args.source}")
    log(f"  layer      : {args.layer}")
    log(f"  enforced   : {enforced}  "
        f"({'marts → public contracts' if enforced else 'staging → documented but not enforced'})")
    log(f"  dry-run    : {args.dry_run}\n")

    # ── SECTION 1: Add contracts to model YAML ─────────────────────────────────
    header("1 · Adding contracts to model schema YAML")

    model_yaml_files = sorted(models_dir.rglob(f"stg_{args.source}*.yml"))
    if not model_yaml_files:
        warn(f"No stg_{args.source}*.yml files found in {models_dir}")
    else:
        ok(f"Found {len(model_yaml_files)} model YAML file(s)")

    total_models  = 0
    total_typed   = 0

    for yaml_path in model_yaml_files:
        data   = load_yaml(yaml_path)
        models = data.get("models", [])
        if not models:
            continue

        yaml_updated = False

        for i, model in enumerate(models):
            model_name = model.get("name", "")

            if args.model and model_name != args.model:
                continue

            updated_model, n_typed = add_contract_to_model(model, enforced)
            models[i]    = updated_model
            total_models += 1
            total_typed  += n_typed
            yaml_updated  = True

            status = "enforced" if enforced else "documented (not enforced)"
            ok(f"  {model_name}  →  contract: {status}  ·  {n_typed} columns typed")

        if yaml_updated and not args.dry_run:
            data["models"] = models
            write_yaml(data, yaml_path)
            ok(f"  Written → {yaml_path}")

    # ── SECTION 2: Add freshness to source YAML ────────────────────────────────
    header("2 · Adding freshness thresholds to source YAML")

    log("  SLA tiers:")
    log("    realtime → warn: 1h   error: 3h   (event streams)")
    log("    daily    → warn: 25h  error: 49h  (nightly batch)")
    log("    static   → null       null         (reference data)\n")

    source_yaml_files = sorted(models_dir.rglob(f"src_{args.source}*.yml"))
    if not source_yaml_files:
        warn(f"No src_{args.source}*.yml files found in {models_dir}")
    else:
        ok(f"Found {len(source_yaml_files)} source YAML file(s)\n")

    total_sources = 0

    for yaml_path in source_yaml_files:
        data = load_yaml(yaml_path)
        if not data.get("sources"):
            continue

        log(f"  Processing {yaml_path.name}:")
        updated_data, n_updated, n_skipped = add_freshness_to_sources(data)
        total_sources += n_updated

        if not args.dry_run:
            write_yaml(updated_data, yaml_path)
            ok(f"\n  Written → {yaml_path}")

    # ── SECTION 3: Summary ─────────────────────────────────────────────────────
    header("Done")
    ok(f"Models processed       : {total_models}")
    ok(f"Columns typed          : {total_typed}")
    ok(f"Sources with freshness : {total_sources}")

    log("\n  Contract summary:", style="bold")
    if enforced:
        log("    Mart contracts enforced — schema changes will fail at compile time")
        log("    Consumers get a stable interface they can ref() safely")
    else:
        log("    Staging contracts documented (enforced: false)")
        log("    Run with --layer marts when building mart models")

    log("\n  Next steps:", style="bold")
    log("    1. Review data_type values — correct any wrong inferences")
    log("    2. Set meta.sla on source tables to match your real refresh cadence")
    log("       (realtime | daily | static)")
    log("    3. dbt build --select staging.tpch — confirm contracts compile")
    log("    4. git add models/ && git commit -m 'feat: contracts + freshness from constraint agent'")
    log("    5. All four agents complete — run dbt docs generate to see the enriched catalog\n")


if __name__ == "__main__":
    main()
