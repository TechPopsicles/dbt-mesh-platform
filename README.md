# dbt-mesh-platform

A production-grade **dbt Mesh** reference implementation on Snowflake Sample Data (TPC-H / TPC-DS),
built to accompany the Medium blog series:

> **"AI-Augmented dbt Best Practices at the Data Platform Level"**
> by [Kiran Pothina](https://github.com/TechPopsicles) · [TechMinion Academy](https://techminionacademy.com)

---

## Blog series

| # | Post | Status |
|---|---|---|
| 1 | [Scaffolding a Production dbt Project in 60 Seconds with AI](https://kiran-pothina.medium.com/scaffolding-a-production-dbt-project-in-60-seconds-with-ai-ad4c43fced4e) | ✅ Published |
| 2 | [Four AI Agents That Keep Your dbt Project Honest](https://kiran-pothina.medium.com/four-ai-agents-that-keep-your-dbt-project-honest-f6bccf5cd465) | ✅ Published |
| 3 | [Scaling dbt Across Teams — Mesh, Contracts and Versioning](https://medium.com/@kiran-pothina/scaling-dbt-across-teams-mesh-contracts-and-versioning-9177bd22e038) | ✅ Published |
| 4 | [One Truth, Five Teams — Governing the dbt Semantic Layer](https://medium.com/@kiran-pothina/one-truth-five-teams-governing-the-dbt-semantic-layer-6b24aaabe873) | ✅ Published |
| 5 | Ship Fast, Break Nothing — CI/CD, Monitoring and Model Health | 🔜 Coming soon |

---

## Project topology

```
dbt-mesh-platform/
├── governance/            ← Snowflake setup SQL · cross-domain grants
├── agents/                ← AI agent scripts (boilerplate · description · test · lineage)
├── profiles.yml.example   ← Connection profiles for all six domain projects
│
├── dbt_platform/          ← Platform team · sources · staging · shared macros
├── dbt_commercial/        ← Commercial team · sales · crm · partnerships
├── dbt_finance/           ← Finance team · ARR · billing · fp&a
├── dbt_product/           ← Product team · events · features · growth · ml
├── dbt_marketing/         ← Marketing team · seo · sem · content · lifecycle
└── dbt_analytics/         ← Analytics team · cross-domain marts · metric registry
```

Each domain project is an independent dbt project with its own:
- Snowflake database (`PLATFORM_DEV` / `PLATFORM_PROD`)
- Warehouse (`PLATFORM_WH`)
- Roles (`PLATFORM_DEV_ROLE` / `PLATFORM_PROD_ROLE`)
- CI/CD pipeline

---

## Snowflake setup

Full setup scripts are in `governance/`. Run them in this order:

### Step 1 — One-time infrastructure setup
```sql
-- Run as USERADMIN — creates all roles
-- governance/snowflake_setup.sql  (Sections 1-2)

-- Run as SYSADMIN — creates warehouses, databases, grants
-- governance/snowflake_setup.sql  (Sections 3-9)
```

Six domain databases created per environment:

| Domain | Dev database | Prod database | Warehouse |
|---|---|---|---|
| Platform | `PLATFORM_DEV` | `PLATFORM_PROD` | `PLATFORM_WH` |
| Commercial | `COMMERCIAL_DEV` | `COMMERCIAL_PROD` | `COMMERCIAL_WH` |
| Finance | `FINANCE_DEV` | `FINANCE_PROD` | `FINANCE_WH` |
| Product | `PRODUCT_DEV` | `PRODUCT_PROD` | `PRODUCT_WH` |
| Marketing | `MARKETING_DEV` | `MARKETING_PROD` | `MARKETING_WH` |
| Analytics | `ANALYTICS_DEV` | `ANALYTICS_PROD` | `ANALYTICS_WH` |

### Step 2 — Generate your RSA key (once per developer)
```bash
mkdir -p ~/.dbt
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM \
  -nocrypt -out ~/.dbt/rsa_key.p8
openssl rsa -in ~/.dbt/rsa_key.p8 -pubout -out ~/.dbt/rsa_key.pub
chmod 600 ~/.dbt/rsa_key.p8
```

Register the public key in Snowflake (run as USERADMIN):
```sql
ALTER USER <your_username> SET RSA_PUBLIC_KEY='<contents of rsa_key.pub>';
```

### Step 3 — Set environment variables
```bash
# Add to ~/.zshrc or ~/.bashrc
export SNOWFLAKE_ACCOUNT="your_account_identifier"   # e.g. abc12345.us-east-1
export SNOWFLAKE_USER="your_snowflake_username"
export SNOWFLAKE_PRIVATE_KEY_PATH="$HOME/.dbt/rsa_key.p8"
export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=""            # leave empty if no passphrase
export DBT_USER_SCHEMA="yourname"                     # scopes dev schemas e.g. kiran_staging
```

### Step 4 — Configure profiles
```bash
cp profiles.yml.example ~/.dbt/profiles.yml
```

Three targets per domain project:

| Target | Database | Schema produced | When used |
|---|---|---|---|
| `dev` | `PLATFORM_DEV` | `kiran_staging` | Local development |
| `ci` | `PLATFORM_DEV` | `pr_12_staging` | GitHub Actions PR run |
| `prod` | `PLATFORM_PROD` | `staging` | Merge to main deploy |

---

## Quick start — dbt_platform

```bash
git clone https://github.com/TechPopsicles/dbt-mesh-platform.git
cd dbt-mesh-platform

# Install dependencies
pip install dbt-core==1.11.8 dbt-snowflake==1.11.4
pip install -r agents/requirements.txt

# Verify connection
cd dbt_platform
dbt deps
dbt debug

# Build TPC-H staging layer
dbt build --select staging.tpch
```

---

## AI boilerplate agent — Post 1 demo

The `agents/generate_boilerplate.py` script connects to Snowflake, reads
`INFORMATION_SCHEMA`, and generates the full staging layer in under 60 seconds.

```bash
# Generate staging layer from scratch
python agents/generate_boilerplate.py \
  --database  SNOWFLAKE_SAMPLE_DATA \
  --schema    TPCH_SF1 \
  --source    tpch \
  --project   dbt_platform \
  --out-dir   dbt_platform/models/staging/tpch

# Preview without writing files
python agents/generate_boilerplate.py \
  --database  SNOWFLAKE_SAMPLE_DATA \
  --schema    TPCH_SF1 \
  --source    tpch \
  --project   dbt_platform \
  --out-dir   dbt_platform/models/staging/tpch \
  --dry-run
```

What it generates for 8 TPC-H tables:
- `src_tpch.yml` — source definitions with column docs
- `stg_tpch__{table}.sql` × 8 — staging models (rename · cast · derive)
- `stg_tpch.yml` — model schema with inferred tests

Agent behaviour:
- Uses existing Snowflake column `COMMENT` as primary description source
- Falls back to name-pattern inference when comments are absent
- Detects composite PKs (lineitem, partsupp) — no false unique test failures
- Overwrites existing files cleanly on every run

---

## Schema naming convention

```
{DOMAIN}_{ENV}.{PREFIX}_{LAYER}

Examples:
  PLATFORM_DEV.KIRAN_STAGING      ← dev (DBT_USER_SCHEMA=kiran)
  PLATFORM_DEV.PR_12_STAGING      ← CI  (DBT_USER_SCHEMA=pr_12)
  PLATFORM_PROD.STAGING           ← prod (DBT_USER_SCHEMA='')
```

Controlled by the `generate_schema_name` macro in
`dbt_platform/macros/generate_schema_name.sql`.

---

## Cross-domain access

Grants are automated via `on-run-end` hooks — no manual SQL after `dbt build`.

```
PLATFORM staging  →  all 5 domain roles can read staging views
COMMERCIAL marts  →  MARKETING (dim_customers only) · FINANCE (fct_deals only) · ANALYTICS (all)
FINANCE marts     →  ANALYTICS (ARR + collections only — FP&A protected)
PRODUCT marts     →  ANALYTICS (all public contracts)
MARKETING marts   →  ANALYTICS (all public contracts)
```

For initial setup and after first build, run:
```bash
# governance/grants.sql — set prefix and env at top of file
# DEV:   prefix := 'kiran';  env := 'DEV';
# PROD:  prefix := '';        env := 'PROD';
```

---

## Repository conventions

| Convention | Pattern | Example |
|---|---|---|
| Database | `{DOMAIN}_{ENV}` | `FINANCE_DEV` |
| Warehouse | `{DOMAIN}_WH` | `FINANCE_WH` |
| Role | `{DOMAIN}_{ENV}_ROLE` | `FINANCE_DEV_ROLE` |
| Schema | `{PREFIX}_{LAYER}` | `KIRAN_STAGING` |
| Source model | `stg_{source}__{entity}` | `stg_tpch__orders` |
| Intermediate | `int_{verb}_{entity}` | `int_order_totals` |
| Fact mart | `fct_{event}_v{n}` | `fct_orders_v2` |
| Dimension mart | `dim_{entity}_v{n}` | `dim_customers_v1` |

---

## What's built so far

- [x] `governance/snowflake_setup.sql` — full Snowflake infrastructure
- [x] `governance/grants.sql` — cross-domain access grants
- [x] `profiles.yml.example` — all six domain profiles · RSA auth · 3 targets
- [x] `agents/generate_boilerplate.py` — AI boilerplate generation agent
- [x] `dbt_platform/` — TPC-H staging layer · source YAML · grant macro · hook
- [ ] `dbt_commercial/` — coming in Post 3
- [ ] `dbt_finance/` — coming in Post 3
- [ ] `dbt_product/` — coming in Post 3
- [ ] `dbt_marketing/` — coming in Post 3
- [ ] `dbt_analytics/` — coming in Post 4
- [ ] `agents/description_agent.py` — coming in Post 2
- [ ] `.github/workflows/` — coming in Post 5

---

License: MIT
