# dbt_platform

The **foundation project** of the dbt-mesh-platform. Every other domain project
(`dbt_commercial`, `dbt_finance`, `dbt_product`, `dbt_marketing`, `dbt_analytics`)
depends on this project for sources, shared macros, and reference data.

## What lives here

| Folder | Contents | Access |
|---|---|---|
| `models/staging/tpch/` | 8 staging models from TPC-H SF1 | public |
| `models/staging/tpcds/` | TPC-DS staging (Session 2+) | public |
| `models/intermediate/` | Cross-source joins owned by platform | public |
| `models/marts/` | Platform-level reference dimensions | public + contracted |
| `macros/` | Shared macros used by all domain projects | — |
| `analyses/` | Snowflake setup SQL + ad-hoc queries | — |

## Staging model conventions

Every staging model follows this pattern:

```
stg_{source}__{entity}.sql
     ↑          ↑
  tpch/tpcds   orders/customers/etc
```

Inside each model:
1. `source` CTE — raw `select *` from source
2. `renamed` CTE — rename + cast + derive simple flags
3. Final `select * from renamed`

No joins in staging. No business logic. No aggregations.

## Running locally

```bash
cd dbt_platform
dbt deps                          # install packages
dbt debug                         # verify connection
dbt build --select staging.tpch   # build + test all TPC-H staging models
dbt docs generate && dbt docs serve
```

## Key source tables

| Staging model | Source table | Rows (SF1) |
|---|---|---|
| `stg_tpch__orders` | `TPCH_SF1.ORDERS` | 1,500,000 |
| `stg_tpch__lineitems` | `TPCH_SF1.LINEITEM` | 6,000,000 |
| `stg_tpch__customers` | `TPCH_SF1.CUSTOMER` | 150,000 |
| `stg_tpch__suppliers` | `TPCH_SF1.SUPPLIER` | 10,000 |
| `stg_tpch__parts` | `TPCH_SF1.PART` | 200,000 |
| `stg_tpch__partsupp` | `TPCH_SF1.PARTSUPP` | 800,000 |
| `stg_tpch__nations` | `TPCH_SF1.NATION` | 25 |
| `stg_tpch__regions` | `TPCH_SF1.REGION` | 5 |
