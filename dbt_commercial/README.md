# dbt_commercial

The **commercial domain project** of the dbt-mesh-platform. Owns customer,
order, and supplier data products. Publishes two public contracts consumed
by Finance, Marketing, and Analytics.

## What lives here

| Layer | Models | Access | Notes |
|---|---|---|---|
| `staging/commercial/` | stg_commercial__orders/customers/lineitems | private | Reads from PLATFORM staging via source() |
| `intermediate/commercial/` | int_order_items, int_customer_orders | private | Never cross-domain referenced |
| `marts/sales/` | fct_orders_v1 | **public · contract enforced** | Consumed by Finance + Analytics |
| `marts/crm/` | dim_customers_v1 | **public · contract enforced** | Consumed by Marketing + Analytics |

## Public contracts

### `fct_orders_v1` — One row per order
Key columns: `order_key`, `customer_key`, `order_date`, `order_status`,
`gross_revenue`, `net_revenue`, `line_item_count`

```sql
-- Consuming in dbt_finance or dbt_analytics
select * from {{ source('commercial', 'fct_orders_v1') }}
-- or with versioning:
select * from {{ ref('fct_orders', v=1) }}
```

### `dim_customers_v1` — One row per customer (no PII)
Key columns: `customer_key`, `nation_key`, `market_segment`, `customer_tier`,
`lifetime_gross_revenue`, `total_orders`, `is_high_value`

```sql
select * from {{ source('commercial', 'dim_customers_v1') }}
```

## Data source

Reads from `PLATFORM_DEV.{PREFIX}_STAGING` — Platform staging views.
Never queries `SNOWFLAKE_SAMPLE_DATA` directly.

Set these env vars before running:
```bash
export PLATFORM_DATABASE=PLATFORM_DEV
export PLATFORM_SCHEMA=KIRAN_STAGING   # your DBT_USER_SCHEMA + _STAGING
```

## Quick start

```bash
cd dbt_commercial
dbt deps
dbt debug
dbt build --select staging.commercial+
```

## Breaking change protocol

When a breaking change is needed on a public contract:

1. Create `fct_orders_v2.sql` with the new schema
2. Update `fct_orders.yml` — set `latest_version: 2`, add `deprecation_date` on v1
3. Run `dbt build` — v1 consumers get compile-time deprecation warnings
4. Notify downstream teams (Finance, Analytics) with migration guide
5. After deprecation window, remove v1

## Versioning

```yaml
# fct_orders.yml
latest_version: 1
versions:
  - v: 1
    defined_in: fct_orders_v1
  # - v: 2              ← uncomment when v2 is ready
  #   defined_in: fct_orders_v2
```
