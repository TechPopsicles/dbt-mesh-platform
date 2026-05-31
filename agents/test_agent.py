#!/usr/bin/env python3
"""
=============================================================================
dbt-mesh-platform  ·  Test Agent
=============================================================================
Profiles actual data in Snowflake staging views and generates accurate
dbt data_tests — replacing placeholder tests with reality-based assertions.

What it profiles per column:
  - null count + rate          → not_null test
  - distinct count vs row count → unique test (sole PKs only)
  - distinct values (≤ threshold) → accepted_values test
  - FK column detection        → relationships test

Test format: dbt 1.9+ data_tests: key with arguments: nesting.
Severity:    not_null / unique on PKs → error (human-verified)
             accepted_values / relationships → warn (AI-generated guardrail)

Design decisions:
  - Profiles staging VIEWS in Snowflake (not raw source tables)
  - Overwrites existing data_tests per column (Git is the audit trail)
  - Cardinality threshold: 10 distinct values for accepted_values
  - One Snowflake session — all profiling in a single connection
  - FK map built automatically from sibling YAML files (no hardcoding)

Usage:
  python agents/test_agent.py \
      --project-dir  dbt_platform \
      --models-dir   dbt_platform/models/staging/tpch \
      --source       tpch \
      --database     PLATFORM_DEV \
      --db-schema    KIRAN_STAGING

Environment variables required (same as boilerplate agent):
  SNOWFLAKE_ACCOUNT
  SNOWFLAKE_USER
  SNOWFLAKE_PRIVATE_KEY_PATH
  SNOWFLAKE_PRIVATE_KEY_PASSPHRASE
  SNOWFLAKE_ROLE      (default: PLATFORM_DEV_ROLE)
  SNOWFLAKE_WAREHOUSE (default: PLATFORM_WH)
=============================================================================
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

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
# SNOWFLAKE CONNECTION
# =============================================================================

def load_private_key(key_path: str, passphrase: str):
    key_path = os.path.expanduser(key_path)
    with open(key_path, "rb") as f:
        raw = f.read()
    pwd = passphrase.encode() if passphrase else None
    pk = serialization.load_pem_private_key(raw, password=pwd,
                                             backend=default_backend())
    return pk.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_connection():
    try:
        import snowflake.connector
    except ImportError:
        err("snowflake-connector-python not installed.")
        sys.exit(1)

    account    = os.environ["SNOWFLAKE_ACCOUNT"]
    user       = os.environ["SNOWFLAKE_USER"]
    key_path   = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "~/.dbt/rsa_key.p8")
    passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")
    role       = os.environ.get("SNOWFLAKE_ROLE", "PLATFORM_DEV_ROLE")
    warehouse  = os.environ.get("SNOWFLAKE_WAREHOUSE", "PLATFORM_WH")

    pkb  = load_private_key(key_path, passphrase)
    conn = snowflake.connector.connect(
        account=account, user=user, private_key=pkb,
        role=role, warehouse=warehouse,
    )
    ok(f"Connected  account={account}  role={role}  warehouse={warehouse}")
    return conn


# =============================================================================
# COLUMN PROFILER
# =============================================================================

def profile_column(
    conn,
    database: str,
    schema: str,
    view: str,
    column: str,
    total_rows: int,
    max_cardinality: int,
) -> dict:
    """
    Profile one column in a Snowflake staging view.
    Returns a dict with null_count, distinct_count, and sample_values.
    """
    cur = conn.cursor()
    fqn = f"{database}.{schema}.{view}"

    # Null count + distinct count in one query
    cur.execute(f"""
        SELECT
            COUNT_IF({column} IS NULL)     AS null_count,
            COUNT(DISTINCT {column})       AS distinct_count
        FROM {fqn}
    """)
    row = cur.fetchone()
    null_count     = row[0] or 0
    distinct_count = row[1] or 0

    # Sample distinct values only if low-cardinality
    sample_values = []
    if 0 < distinct_count <= max_cardinality:
        cur.execute(f"""
            SELECT DISTINCT {column}
            FROM {fqn}
            WHERE {column} IS NOT NULL
            ORDER BY {column}
            LIMIT {max_cardinality}
        """)
        sample_values = [str(r[0]) for r in cur.fetchall()]

    return {
        "null_count":     null_count,
        "null_rate":      null_count / total_rows if total_rows > 0 else 0,
        "distinct_count": distinct_count,
        "is_unique":      distinct_count == total_rows and null_count == 0,
        "sample_values":  sample_values,
    }


def get_row_count(conn, database: str, schema: str, view: str) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {database}.{schema}.{view}")
    return cur.fetchone()[0]


# =============================================================================
# FK MAP BUILDER
# =============================================================================

def build_fk_map(models_dir: Path, source: str) -> dict[str, str]:
    """
    Scan all stg_{source}.yml files and build a map of:
      column_name  →  model_name that owns that column as a PK

    Used to generate relationships tests automatically.
    """
    fk_map: dict[str, str] = {}

    for yaml_path in models_dir.rglob(f"stg_{source}*.yml"):
        data = yaml.safe_load(yaml_path.read_text()) or {}
        for model in data.get("models", []):
            model_name = model.get("name", "")
            columns    = model.get("columns", [])
            if not columns:
                continue

            # The first column or column with unique+not_null is the PK
            for col in columns:
                col_name = col.get("name", "")
                tests    = col.get("data_tests", col.get("tests", []))
                test_names = []
                for t in tests:
                    if isinstance(t, str):
                        test_names.append(t)
                    elif isinstance(t, dict):
                        test_names.extend(t.keys())

                if "unique" in test_names and "not_null" in test_names:
                    # This is the PK of this model — register it
                    fk_map[col_name] = model_name
                    break

    ok(f"FK map built — {len(fk_map)} primary keys indexed")
    for col, model in fk_map.items():
        log(f"    {col}  →  {model}")

    return fk_map


# =============================================================================
# TEST GENERATOR
# =============================================================================

def is_pk_column(col: dict) -> bool:
    """Check if column already has unique + not_null (human-verified PK)."""
    tests = col.get("data_tests", col.get("tests", []))
    test_names = []
    for t in tests:
        if isinstance(t, str):
            test_names.append(t)
        elif isinstance(t, dict):
            test_names.extend(t.keys())
    return "unique" in test_names and "not_null" in test_names


def generate_data_tests(
    col_name:       str,
    profile:        dict,
    col_meta:       dict,
    fk_map:         dict[str, str],
    total_rows:     int,
    all_cols:       list[dict],
    max_cardinality: int,
) -> list[Any]:
    """
    Generate data_tests list for a column based on its profile.

    Rules:
      not_null   → null_rate == 0 AND column is not a free-text comment field
      unique     → is_unique AND sole PK (only one KEY col at ordinal 1)
      accepted_values → distinct_count ≤ max_cardinality AND not a key column
      relationships   → column name matches a known PK in fk_map
    """
    tests        = []
    null_rate    = profile["null_rate"]
    distinct     = profile["distinct_count"]
    is_unique    = profile["is_unique"]
    values       = profile["sample_values"]
    name_lower   = col_name.lower()

    # Skip comment/free-text columns — noisy tests, low value
    is_comment = any(x in name_lower for x in ["comment", "note", "remark", "description"])

    # ── not_null ──────────────────────────────────────────────────────────────
    if null_rate == 0 and not is_comment:
        tests.append("not_null")

    # ── unique — only for confirmed sole PKs ──────────────────────────────────
    # Count KEY columns at low ordinal to detect composite keys
    key_cols = [
        c for c in all_cols
        if c.get("name", "").lower().endswith("_key")
        or c.get("name", "").lower().endswith("key")
    ]
    is_sole_pk = (
        is_unique
        and (name_lower.endswith("key") or name_lower.endswith("_key"))
        and col_meta.get("ordinal", 99) == 1
        and len(key_cols) == 1
    )
    if is_sole_pk:
        tests.append("unique")

    # ── relationships — FK detection ──────────────────────────────────────────
    # Column ends in _key, is not the sole PK, and maps to a known model
    is_fk = (
        (name_lower.endswith("_key") or name_lower.endswith("key"))
        and not is_sole_pk
        and col_name in fk_map
    )
    if is_fk:
        target_model = fk_map[col_name]
        tests.append({
            "relationships": {
                "arguments": {
                    "to":    f"ref('{target_model}')",
                    "field": col_name,
                },
                "config": {
                    "severity": "warn",   # AI-generated guardrail
                },
            }
        })

    # ── accepted_values — low-cardinality categoricals ────────────────────────
    is_key_col  = name_lower.endswith("key") or name_lower.endswith("_key")
    is_flag_col = name_lower.startswith("is_") or name_lower.startswith("has_")

    if (
        values
        and not is_key_col
        and not is_comment
        and 1 < distinct <= max_cardinality
    ):
        tests.append({
            "accepted_values": {
                "arguments": {
                    "values": values,
                },
                "config": {
                    "severity": "warn",   # AI-generated guardrail
                },
            }
        })

    return tests


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


def model_to_view_name(model_name: str) -> str:
    """Convert dbt model name to Snowflake view name (uppercase)."""
    return model_name.upper()


# =============================================================================
# MAIN
# =============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Profile Snowflake staging views and generate dbt data_tests"
    )
    p.add_argument("--project-dir",      required=True,
                   help="dbt project root  e.g. dbt_platform")
    p.add_argument("--models-dir",       required=True,
                   help="Staging models path  e.g. dbt_platform/models/staging/tpch")
    p.add_argument("--source",           required=True,
                   help="Source name  e.g. tpch")
    p.add_argument("--database",         required=True,
                   help="Snowflake database containing staging views  e.g. PLATFORM_DEV")
    p.add_argument("--db-schema",        required=True,
                   help="Snowflake schema containing staging views  e.g. KIRAN_STAGING")
    p.add_argument("--model",            default=None,
                   help="Run on a single model  e.g. stg_tpch__orders")
    p.add_argument("--max-cardinality",  type=int, default=10,
                   help="Max distinct values for accepted_values test (default: 10)")
    p.add_argument("--dry-run",          action="store_true",
                   help="Print generated tests without writing files")
    return p.parse_args()


def main() -> None:
    args       = parse_args()
    models_dir = Path(args.models_dir)

    header("dbt Test Agent")
    log(f"  project-dir     : {args.project_dir}")
    log(f"  models-dir      : {models_dir}")
    log(f"  source          : {args.source}")
    log(f"  database        : {args.database}")
    log(f"  schema          : {args.db_schema}")
    log(f"  model           : {args.model or 'all'}")
    log(f"  max-cardinality : {args.max_cardinality}")
    log(f"  dry-run         : {args.dry_run}\n")

    # ── Connect to Snowflake ───────────────────────────────────────────────────
    header("1 · Connecting to Snowflake")
    conn = get_connection()

    # ── Build FK map from existing YAML files ──────────────────────────────────
    header("2 · Building FK relationship map")
    fk_map = build_fk_map(models_dir, args.source)

    # ── Find YAML files ────────────────────────────────────────────────────────
    header("3 · Finding staging YAML files")
    yaml_files = sorted(models_dir.rglob(f"stg_{args.source}*.yml"))
    if not yaml_files:
        err(f"No stg_{args.source}*.yml files found in {models_dir}")
        sys.exit(1)
    ok(f"Found {len(yaml_files)} YAML file(s)")

    # ── Process each model ─────────────────────────────────────────────────────
    header("4 · Profiling columns and generating data_tests")

    total_models   = 0
    total_tests    = 0
    total_columns  = 0

    for yaml_path in yaml_files:
        data   = load_yaml(yaml_path)
        models = data.get("models", [])
        if not models:
            continue

        yaml_updated = False

        for i, model in enumerate(models):
            model_name = model.get("name", "")

            if args.model and model_name != args.model:
                continue

            view_name = model_to_view_name(model_name)
            columns   = model.get("columns", [])
            if not columns:
                warn(f"  {model_name} — no columns, skipping")
                continue

            log(f"\n  📊 {model_name}")

            # Get total row count for this view
            try:
                total_rows = get_row_count(
                    conn, args.database, args.db_schema, view_name
                )
                ok(f"  Row count: {total_rows:,}")
            except Exception as e:
                err(f"  Could not query {view_name}: {e}")
                err(f"  Check --database ({args.database}) and --db-schema ({args.db_schema})")
                continue

            model_tests   = 0
            columns_updated = 0

            for j, col in enumerate(columns):
                col_name = col.get("name", "")

                # Profile this column
                try:
                    profile = profile_column(
                        conn        = conn,
                        database    = args.database,
                        schema      = args.db_schema,
                        view        = view_name,
                        column      = col_name,
                        total_rows  = total_rows,
                        max_cardinality = args.max_cardinality,
                    )
                except Exception as e:
                    warn(f"    Could not profile {col_name}: {e}")
                    continue

                # Generate data_tests from profile
                col_meta = {"ordinal": j + 1}
                new_tests = generate_data_tests(
                    col_name        = col_name,
                    profile         = profile,
                    col_meta        = col_meta,
                    fk_map          = fk_map,
                    total_rows      = total_rows,
                    all_cols        = columns,
                    max_cardinality = args.max_cardinality,
                )

                # Log what changed
                old_tests = col.get("data_tests", col.get("tests", []))
                if new_tests != old_tests:
                    test_names = []
                    for t in new_tests:
                        if isinstance(t, str):
                            test_names.append(t)
                        elif isinstance(t, dict):
                            test_names.append(list(t.keys())[0])

                    null_pct = f"{profile['null_rate']*100:.1f}%"
                    dist_str = f"{profile['distinct_count']:,} distinct"
                    log(f"    {col_name:<30} nulls={null_pct}  {dist_str}  → {test_names}")
                    columns_updated += 1

                if args.dry_run:
                    continue

                # Remove old tests key (handles both tests: and data_tests:)
                col.pop("tests", None)
                col.pop("data_tests", None)

                # Write data_tests in dbt 1.9+ format
                if new_tests:
                    col["data_tests"] = new_tests

                columns[j]  = col
                model_tests += len(new_tests)
                total_tests += len(new_tests)

            total_columns  += len(columns)
            total_models   += 1

            ok(f"  {model_name} — {columns_updated} columns updated, {model_tests} tests generated")

            if not args.dry_run:
                models[i]    = {**model, "columns": columns}
                yaml_updated = True

        if yaml_updated and not args.dry_run:
            data["models"] = models
            write_yaml(data, yaml_path)
            ok(f"\n  Written → {yaml_path}")

    conn.close()

    # ── Summary ────────────────────────────────────────────────────────────────
    header("Done")
    ok(f"Models processed  : {total_models}")
    ok(f"Columns profiled  : {total_columns}")
    ok(f"Tests generated   : {total_tests}")

    log("\n  Next steps:", style="bold")
    log("    1. Review generated data_tests in your YAML files")
    log("    2. Upgrade any warn severity tests you've manually verified to error")
    log("    3. dbt build --select staging.tpch  — confirm zero test failures")
    log("    4. git add models/ && git commit -m 'feat: reality-based data_tests from test agent'")
    log("    5. Run lineage_agent.py to surface blast radius and orphaned sources\n")


if __name__ == "__main__":
    main()
