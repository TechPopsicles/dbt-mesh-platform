#!/usr/bin/env python3
"""
=============================================================================
dbt-mesh-platform  ·  Boilerplate Generation Agent
=============================================================================
Connects to Snowflake, introspects a source schema via INFORMATION_SCHEMA,
and generates three file types per source table:

  1. src_{source}.yml        — dbt source definition with column docs
  2. stg_{source}__{table}.sql — staging model (rename · cast · derive)
  3. stg_{source}.yml        — model schema with column-level tests

Usage:
  python agents/generate_boilerplate.py \\
      --database  SNOWFLAKE_SAMPLE_DATA \\
      --schema    TPCH_SF1 \\
      --source    tpch \\
      --project   dbt_platform \\
      --out-dir   dbt_platform/models/staging/tpch

All Snowflake credentials are read from environment variables — never
hardcoded. Set these before running:
  export SNOWFLAKE_ACCOUNT=...
  export SNOWFLAKE_USER=...
  export SNOWFLAKE_PRIVATE_KEY_PATH=~/.dbt/rsa_key.p8
  export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=''
  export SNOWFLAKE_ROLE=PLATFORM_DEV_ROLE
  export SNOWFLAKE_WAREHOUSE=PLATFORM_WH

Install dependencies:
  pip install snowflake-connector-python cryptography pyyaml jinja2
=============================================================================
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

# ── Optional rich output ───────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
    def log(msg, style=""):    console.print(msg, style=style)
    def ok(msg):               console.print(f"  ✅ {msg}", style="green")
    def warn(msg):             console.print(f"  ⚠️  {msg}", style="yellow")
    def err(msg):              console.print(f"  ❌ {msg}", style="red")
    def header(msg):           console.rule(f"[bold cyan]{msg}[/bold cyan]")
except ImportError:
    def log(msg, style=""):    print(msg)
    def ok(msg):               print(f"  OK  {msg}")
    def warn(msg):             print(f"  WARN {msg}")
    def err(msg):              print(f"  ERR  {msg}")
    def header(msg):           print(f"\n{'='*60}\n{msg}\n{'='*60}")


# =============================================================================
# SNOWFLAKE CONNECTION
# =============================================================================

def load_private_key(key_path: str, passphrase: str):
    """Load RSA private key from .p8 file."""
    key_path = os.path.expanduser(key_path)
    with open(key_path, "rb") as f:
        raw = f.read()
    pwd = passphrase.encode() if passphrase else None
    private_key = serialization.load_pem_private_key(
        raw, password=pwd, backend=default_backend()
    )
    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_connection():
    """Create Snowflake connection from environment variables."""
    try:
        import snowflake.connector
    except ImportError:
        err("snowflake-connector-python not installed.")
        err("Run: pip install snowflake-connector-python cryptography")
        sys.exit(1)

    account    = os.environ["SNOWFLAKE_ACCOUNT"]
    user       = os.environ["SNOWFLAKE_USER"]
    key_path   = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "~/.dbt/rsa_key.p8")
    passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")
    role       = os.environ.get("SNOWFLAKE_ROLE", "PLATFORM_DEV_ROLE")
    warehouse  = os.environ.get("SNOWFLAKE_WAREHOUSE", "PLATFORM_WH")

    pkb = load_private_key(key_path, passphrase)

    conn = snowflake.connector.connect(
        account=account,
        user=user,
        private_key=pkb,
        role=role,
        warehouse=warehouse,
    )
    ok(f"Connected  account={account}  role={role}  warehouse={warehouse}")
    return conn


# =============================================================================
# SCHEMA INTROSPECTION
# =============================================================================

def get_table_comments(conn, database: str, schema: str) -> dict[str, str]:
    """
    Query INFORMATION_SCHEMA.TABLES for table-level comments.
    Returns dict keyed by table_name → comment string.
    """
    sql = f"""
        SELECT
            TABLE_NAME,
            COMMENT
        FROM {database}.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = '{schema.upper()}'
        ORDER BY TABLE_NAME
    """
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    return {
        row[0].lower(): row[1] or ""
        for row in rows
    }


def get_columns(conn, database: str, schema: str) -> dict[str, list[dict]]:
    """
    Query INFORMATION_SCHEMA.COLUMNS for all tables in the schema.
    Returns dict keyed by table_name → list of column dicts.
    Preserves existing Snowflake column comments — used as primary description source.
    """
    sql = f"""
        SELECT
            TABLE_NAME,
            COLUMN_NAME,
            ORDINAL_POSITION,
            DATA_TYPE,
            IS_NULLABLE,
            CHARACTER_MAXIMUM_LENGTH,
            NUMERIC_PRECISION,
            NUMERIC_SCALE,
            COMMENT
        FROM {database}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{schema.upper()}'
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols_map: dict[str, list[dict]] = {}
    for row in rows:
        tname = row[0].lower()
        cols_map.setdefault(tname, []).append({
            "table_name":    row[0].lower(),
            "column_name":   row[1].lower(),
            "ordinal":       row[2],
            "data_type":     row[3],
            "is_nullable":   row[4],
            "char_length":   row[5],
            "num_precision": row[6],
            "num_scale":     row[7],
            "comment":       row[8] or "",
        })
    ok(f"Found {len(cols_map)} tables in {database}.{schema}")
    return cols_map


def get_row_counts(conn, database: str, schema: str,
                   tables: list[str]) -> dict[str, int]:
    """Get approximate row counts for each table."""
    counts = {}
    cur = conn.cursor()
    for table in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {database}.{schema}.{table}")
            counts[table.lower()] = cur.fetchone()[0]
        except Exception:
            counts[table.lower()] = 0
    return counts


# =============================================================================
# TYPE MAPPING
# =============================================================================

# Snowflake data type → dbt/SQL cast target
SNOWFLAKE_TO_CAST: dict[str, str] = {
    "TEXT":             "varchar",
    "VARCHAR":          "varchar",
    "CHAR":             "varchar",
    "CHARACTER":        "varchar",
    "STRING":           "varchar",
    "NUMBER":           "number",
    "DECIMAL":          "number",
    "NUMERIC":          "number",
    "INT":              "integer",
    "INTEGER":          "integer",
    "BIGINT":           "bigint",
    "SMALLINT":         "smallint",
    "FLOAT":            "float",
    "FLOAT4":           "float",
    "FLOAT8":           "float",
    "DOUBLE":           "float",
    "REAL":             "float",
    "BOOLEAN":          "boolean",
    "DATE":             "date",
    "DATETIME":         "timestamp_ntz",
    "TIME":             "time",
    "TIMESTAMP":        "timestamp_ntz",
    "TIMESTAMP_NTZ":    "timestamp_ntz",
    "TIMESTAMP_LTZ":    "timestamp_ltz",
    "TIMESTAMP_TZ":     "timestamp_tz",
    "VARIANT":          "variant",
    "OBJECT":           "object",
    "ARRAY":            "array",
}

# Snowflake data type → dbt schema test suggestions
TYPE_TESTS: dict[str, list[str]] = {
    "DATE":          ["not_null"],
    "TIMESTAMP_NTZ": ["not_null"],
    "BOOLEAN":       ["not_null"],
    "NUMBER":        ["not_null"],
    "INTEGER":       ["not_null"],
    "BIGINT":        ["not_null"],
    "VARCHAR":       [],
    "TEXT":          [],
}


def sf_to_cast(sf_type: str) -> str:
    return SNOWFLAKE_TO_CAST.get(sf_type.upper(), sf_type.lower())


def col_to_snake(name: str) -> str:
    """Convert raw column name to snake_case business name."""
    # Remove common source prefixes (O_, L_, C_, S_, P_, N_, R_, PS_)
    cleaned = re.sub(r'^[A-Z]{1,2}_', '', name, flags=re.IGNORECASE)
    return cleaned.lower()


def infer_description(col_name: str, table_name: str, data_type: str,
                      existing_comment: str = "") -> str:
    """
    Return description in priority order:
      1. Existing Snowflake column COMMENT  — use as-is if present
      2. Inferred from column name + type   — fallback if comment is empty
    The description agent (Post 2) will enrich inferred descriptions with
    richer business context via the Anthropic API.
    """
    # Priority 1 — use existing Snowflake comment verbatim
    if existing_comment and existing_comment.strip():
        return existing_comment.strip()

    # Priority 2 — infer from column name patterns
    name = col_name.lower()

    # Primary / foreign key patterns
    if name.endswith('key') or name.endswith('_key'):
        if 'surr' in name or name == table_name.rstrip('s') + '_key':
            return f"Surrogate primary key for {table_name}."
        return f"Foreign key reference."

    # Date patterns
    if data_type in ('DATE', 'TIMESTAMP_NTZ', 'DATETIME'):
        return f"Date/timestamp for {name.replace('_', ' ')}. Cast to {sf_to_cast(data_type)}."

    # Amount / price / cost patterns
    if any(x in name for x in ['price', 'cost', 'amount', 'revenue', 'balance']):
        return f"Monetary value for {name.replace('_', ' ')}."

    # Flag / boolean patterns
    if name.startswith('is_') or name.startswith('has_'):
        return f"Boolean flag — {name.replace('_', ' ')}."

    # Status / type patterns
    if any(x in name for x in ['status', 'type', 'mode', 'flag']):
        return f"Categorical field — {name.replace('_', ' ')}."

    # Rate / ratio patterns
    if any(x in name for x in ['rate', 'ratio', 'pct', 'percent']):
        return f"Rate or ratio field — {name.replace('_', ' ')}."

    # Comment / description catch-all
    if any(x in name for x in ['comment', 'description', 'note', 'remark']):
        return f"Free-text comment field. Not used in analytics."

    return f"{name.replace('_', ' ').capitalize()}."


def infer_tests(col: dict, table_name: str, all_cols: list[dict]) -> list[Any]:
    """Infer appropriate dbt tests from column metadata."""
    tests = []
    name      = col["column_name"]
    snake     = col_to_snake(name)
    dtype     = col["data_type"].upper()
    nullable  = col["is_nullable"] == "YES"

    # ── Primary key detection ─────────────────────────────────────────────────
    # A column is the sole PK only if:
    #   (a) it ends with KEY and is ordinal 1, AND
    #   (b) no other column in the table also ends with KEY at low ordinal
    #       (which would indicate a composite key relationship)
    # Composite key tables (lineitem, partsupp) get surrogate key in SQL —
    # not_null is added but unique is skipped to avoid false failures.
    key_cols = [
        c for c in all_cols
        if c["column_name"].upper().endswith("KEY") and c["ordinal"] <= 3
    ]
    is_sole_pk = (
        name.upper().endswith("KEY")
        and col["ordinal"] == 1
        and len(key_cols) == 1          # only one KEY column → single PK table
    )
    is_fk = (
        name.upper().endswith("KEY")
        and col["ordinal"] > 1          # FK columns — not_null only, no unique
    )
    is_composite_pk_member = (
        name.upper().endswith("KEY")
        and col["ordinal"] == 1
        and len(key_cols) > 1           # multiple KEY cols → composite PK table
    )

    if is_sole_pk:
        tests += ["not_null", "unique"]
        return tests

    if is_composite_pk_member or is_fk:
        tests.append("not_null")
        return tests

    # ── not_null for non-nullable non-key columns ─────────────────────────────
    if not nullable and dtype not in ("TEXT", "VARCHAR", "STRING"):
        tests.append("not_null")

    # ── accepted_values: skip placeholder — description agent handles this ────
    # Placeholder accepted_values always fail and create noise.
    # The test agent (Post 2) profiles real distinct values and generates
    # correct accepted_values tests. Skip here to keep build clean.

    return tests


# =============================================================================
# FILE GENERATORS
# =============================================================================

def generate_source_yaml(
    source_name: str,
    database: str,
    schema: str,
    cols_map: dict[str, list[dict]],
    row_counts: dict[str, int],
    table_comments: dict[str, str] | None = None,
) -> str:
    """Generate src_{source}.yml — dbt source definition.
    Uses existing Snowflake table/column comments as primary descriptions.
    Falls back to inferred descriptions only when comments are absent.
    """
    table_comments = table_comments or {}

    tables = []
    for table_name, cols in sorted(cols_map.items()):
        col_list = []
        for col in cols:
            snake = col_to_snake(col["column_name"])
            col_list.append({
                "name":        col["column_name"].upper(),
                "description": infer_description(
                    snake, table_name, col["data_type"],
                    existing_comment=col.get("comment", ""),
                ),
            })

        # Use existing table comment if present, otherwise generate placeholder
        existing_table_comment = table_comments.get(table_name, "")
        table_description = (
            existing_table_comment.strip()
            if existing_table_comment.strip()
            else (
                f"Source table {table_name.upper()} from {database}.{schema}. "
                f"Grain: TBD. Row count: ~{row_counts.get(table_name, 0):,}."
            )
        )

        tables.append({
            "name":        table_name.upper(),
            "description": table_description,
            "config": {
                "meta": {
                    "grain":             "TBD — set after reviewing source docs",
                    "row_count_approx":  row_counts.get(table_name, 0),
                    "owner":             "platform-team@techminionacademy.com",
                },
                "freshness": None,    # static source — no freshness check
            },
            "columns":   col_list,
        })

    doc = {
        "version": 2,
        "sources": [{
            "name":        source_name,
            "description": f"Source schema {schema} from {database}. "
                           f"Auto-generated by agents/generate_boilerplate.py.",
            "database":    database,
            "schema":      schema,
            "meta": {
                "owner":         "platform-team@techminionacademy.com",
                "source_system": database,
                "domain":        source_name,
                "sla":           "static",
            },
            "freshness": {
                "warn_after":  {"count": 7,  "period": "day"},
                "error_after": {"count": 30, "period": "day"},
            },
            "tables": tables,
        }],
    }
    return yaml.dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def generate_staging_sql(
    source_name: str,
    table_name: str,
    cols: list[dict],
) -> str:
    """Generate stg_{source}__{table}.sql — rename · cast · derive."""

    lines = []
    has_composite_key = False
    pk_cols = []

    # Detect surrogate key candidates
    for col in cols:
        snake = col_to_snake(col["column_name"])
        if col["ordinal"] == 1 and col["column_name"].endswith("KEY"):
            pk_cols.append(col["column_name"])

    # Detect composite keys (no single PK)
    if len(pk_cols) == 0:
        has_composite_key = True
        pk_cols = [c["column_name"] for c in cols[:2]]

    # Generate renamed columns
    for col in cols:
        raw   = col["column_name"].upper()
        snake = col_to_snake(col["column_name"])
        dtype = col["data_type"].upper()
        cast  = sf_to_cast(dtype)
        pad   = max(1, 52 - len(raw))

        # Cast dates explicitly
        if dtype in ("DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ"):
            lines.append(f"        cast({raw} as {cast}){' ' * max(1, pad - len(cast) - 7)} as {snake},")
        else:
            lines.append(f"        {raw}{' ' * pad} as {snake},")

    # Remove trailing comma on last line
    if lines:
        lines[-1] = lines[-1].rstrip(",")

    # Surrogate key block
    surrogate = ""
    if has_composite_key:
        key_list = ", ".join(f"'{c.lower()}'" for c in pk_cols)
        surrogate = (
            f"        {{{{ dbt_utils.generate_surrogate_key([{key_list}]) }}}}"
            f"  as {table_name}_key,\n"
        )

    sql = f"""with source as (

    select * from {{{{ source('{source_name}', '{table_name.upper()}') }}}}

),

renamed as (

    select
{surrogate}{''.join(f'{ln}{chr(10)}' for ln in lines)}
    from source

)

select * from renamed
"""
    return sql


def generate_model_yaml(
    source_name: str,
    table_name: str,
    cols: list[dict],
    project_name: str,
) -> dict:
    """Generate model entry for stg_{source}.yml."""

    model_name = f"stg_{source_name}__{table_name}"
    col_entries = []

    for col in cols:
        snake  = col_to_snake(col["column_name"])
        tests  = infer_tests(col, table_name, cols)
        entry: dict[str, Any] = {
            "name":        snake,
            "description": infer_description(
                snake, table_name, col["data_type"],
                existing_comment=col.get("comment", ""),
            ),
        }
        if tests:
            entry["tests"] = tests
        col_entries.append(entry)

    return {
        "name":        model_name,
        "description": (
            f"Staged and renamed records from {source_name.upper()}.{table_name.upper()}. "
            f"One row per {table_name}. "
            f"Renamed columns to snake_case business names. "
            f"Enrich this description with business context."
        ),
        "config": {
            "tags": ["platform", "staging", source_name],
            "meta": {
                "owner":  "platform-team@techminionacademy.com",
                "grain":  "TBD",
                "access": "public",
            },
        },
        "columns": col_entries,
    }


# =============================================================================
# MAIN
# =============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate dbt boilerplate from Snowflake INFORMATION_SCHEMA"
    )
    p.add_argument("--database",  required=True,
                   help="Source database  e.g. SNOWFLAKE_SAMPLE_DATA")
    p.add_argument("--schema",    required=True,
                   help="Source schema    e.g. TPCH_SF1")
    p.add_argument("--source",    required=True,
                   help="dbt source name  e.g. tpch")
    p.add_argument("--project",   required=True,
                   help="dbt project name e.g. dbt_platform")
    p.add_argument("--out-dir",   required=True,
                   help="Output directory e.g. dbt_platform/models/staging/tpch")
    p.add_argument("--tables",    nargs="*", default=None,
                   help="Specific tables to process (default: all)")
    p.add_argument("--dry-run",   action="store_true",
                   help="Print files to stdout instead of writing to disk")
    p.add_argument("--overwrite", action="store_true", default=True,
                   help="Overwrite existing files (default: True — always regenerate cleanly)")
    return p.parse_args()


def write_file(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        log(f"\n{'─'*60}\n📄 {path}\n{'─'*60}")
        log(content)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content)
        if existed:
            warn(f"Overwritten  {path}")
        else:
            ok(f"Written      {path}")


def main() -> None:
    args = parse_args()

    header("dbt Boilerplate Generation Agent")
    log(f"  database  : {args.database}")
    log(f"  schema    : {args.schema}")
    log(f"  source    : {args.source}")
    log(f"  project   : {args.project}")
    log(f"  out-dir   : {args.out_dir}")
    log(f"  dry-run   : {args.dry_run}\n")

    # ── Connect ────────────────────────────────────────────────────────────────
    header("1 · Connecting to Snowflake")
    conn = get_connection()

    # ── Introspect ─────────────────────────────────────────────────────────────
    header("2 · Introspecting schema")
    cols_map = get_columns(conn, args.database, args.schema)

    if args.tables:
        cols_map = {k: v for k, v in cols_map.items()
                    if k.upper() in [t.upper() for t in args.tables]}
        ok(f"Filtered to {len(cols_map)} tables: {list(cols_map.keys())}")

    table_names = list(cols_map.keys())
    log(f"\n  Tables found: {', '.join(table_names)}\n")

    header("3 · Fetching row counts")
    row_counts = get_row_counts(conn, args.database, args.schema, table_names)
    for t, n in row_counts.items():
        ok(f"{t:<20}  {n:>12,} rows")

    header("3b · Fetching existing table comments")
    table_comments = get_table_comments(conn, args.database, args.schema)
    commented = sum(1 for v in table_comments.values() if v.strip())
    ok(f"{commented}/{len(table_comments)} tables have existing Snowflake comments")

    # Log column comment coverage
    col_comment_count = sum(
        1 for cols in cols_map.values()
        for col in cols if col.get("comment", "").strip()
    )
    col_total = sum(len(cols) for cols in cols_map.values())
    ok(f"{col_comment_count}/{col_total} columns have existing Snowflake comments")
    if col_comment_count > 0:
        ok("Existing comments will be used as primary descriptions ✓")
    else:
        warn("No existing comments found — using inferred descriptions")
        warn("Add comments in Snowflake or run the description agent (Post 2) to enrich")

    conn.close()

    # ── Generate files ─────────────────────────────────────────────────────────
    out = Path(args.out_dir)

    header("4 · Generating src_{source}.yml")
    src_yaml = generate_source_yaml(
        args.source, args.database, args.schema, cols_map, row_counts,
        table_comments=table_comments,
    )
    write_file(out / f"src_{args.source}.yml", src_yaml, args.dry_run)

    header("5 · Generating stg_{source}__{table}.sql  (one per table)")
    for table_name, cols in sorted(cols_map.items()):
        sql = generate_staging_sql(args.source, table_name, cols)
        fname = f"stg_{args.source}__{table_name}.sql"
        write_file(out / fname, sql, args.dry_run)

    header("6 · Generating stg_{source}.yml  (model schema + tests)")
    model_entries = []
    for table_name, cols in sorted(cols_map.items()):
        model_entries.append(
            generate_model_yaml(args.source, table_name, cols, args.project)
        )

    model_doc = {"version": 2, "models": model_entries}
    model_yaml = yaml.dump(
        model_doc, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    write_file(out / f"stg_{args.source}.yml", model_yaml, args.dry_run)

    # ── Summary ────────────────────────────────────────────────────────────────
    header("Done")
    total_files = 1 + len(cols_map) + 1   # src yml + N sql + model yml
    ok(f"{total_files} files generated for {len(cols_map)} tables")
    log("\n  Next steps:", style="bold")
    log("    1. Review generated files and add business context to descriptions")
    log("    2. Add PII tags to sensitive columns (name · address · phone · email)")
    log("    3. Add relationship tests between FK and PK columns")
    log("    4. Run:  dbt build --select staging." + args.source)
    log("    5. Run description agent to enrich column descriptions with AI\n")


if __name__ == "__main__":
    main()
