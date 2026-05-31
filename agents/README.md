# agents/

AI agent scripts for the dbt-mesh-platform. Each agent covers one post
in the blog series.

## Agent inventory

| Agent | Post | Status |
|---|---|---|
| `generate_boilerplate.py` | Post 1 — Scaffolding in 60s | ✅ |
| `description_agent.py` | Post 2 — Four AI Agents | 🔜 |
| `test_agent.py` | Post 2 — Four AI Agents | 🔜 |
| `lineage_agent.py` | Post 2 — Four AI Agents | 🔜 |
| `constraint_agent.py` | Post 2 — Four AI Agents | 🔜 |
| `scorecard.py` | Post 5 — CI/CD + Health | 🔜 |

## Setup

```bash
cd dbt-mesh-platform
pip install -r agents/requirements.txt
```

## generate_boilerplate.py

Connects to Snowflake, reads `INFORMATION_SCHEMA.COLUMNS`, and generates:
- `src_{source}.yml` — dbt source definition
- `stg_{source}__{table}.sql` — staging SQL (rename · cast · derive)
- `stg_{source}.yml` — model schema with inferred tests

### Environment variables required

```bash
export SNOWFLAKE_ACCOUNT="your_account"
export SNOWFLAKE_USER="your_username"
export SNOWFLAKE_PRIVATE_KEY_PATH="~/.dbt/rsa_key.p8"
export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=""
export SNOWFLAKE_ROLE="PLATFORM_DEV_ROLE"
export SNOWFLAKE_WAREHOUSE="PLATFORM_WH"
```

### Delete existing files first (clean demo run)

```bash
cd dbt_platform/models/staging/tpch
rm -f src_tpch.yml stg_tpch.yml stg_tpch__*.sql
cd ../../../..
```

### Run — TPC-H SF1

```bash
python agents/generate_boilerplate.py \
  --database  SNOWFLAKE_SAMPLE_DATA \
  --schema    TPCH_SF1 \
  --source    tpch \
  --project   dbt_platform \
  --out-dir   dbt_platform/models/staging/tpch
```

### Dry run (preview without writing files)

```bash
python agents/generate_boilerplate.py \
  --database  SNOWFLAKE_SAMPLE_DATA \
  --schema    TPCH_SF1 \
  --source    tpch \
  --project   dbt_platform \
  --out-dir   dbt_platform/models/staging/tpch \
  --dry-run
```

### Specific tables only

```bash
python agents/generate_boilerplate.py \
  --database  SNOWFLAKE_SAMPLE_DATA \
  --schema    TPCH_SF1 \
  --source    tpch \
  --project   dbt_platform \
  --out-dir   dbt_platform/models/staging/tpch \
  --tables    ORDERS LINEITEM CUSTOMER
```

### What the agent generates vs what you add

| Agent generates | You add after |
|---|---|
| Column renames to snake_case | Business context in descriptions |
| Type casts (date · number · etc) | PII tags on sensitive columns |
| `not_null` + `unique` on PK columns | Relationship tests (FK → PK) |
| `accepted_values` placeholders | Real accepted values from source docs |
| Surrogate keys on composite PKs | Derived measures (net_price · gross_price) |

The delta between agent output and enriched output is the "what AI gives
you vs what you add" section of Blog Post 1.

### Commit strategy for the blog demo

```bash
# Commit 1 — raw agent output
git add dbt_platform/models/staging/tpch/
git commit -m "feat: agent-generated TPC-H staging layer"

# Commit 2 — human enhancements
git commit -m "feat: enhance staging with business context, PII tags, relationship tests"
```
