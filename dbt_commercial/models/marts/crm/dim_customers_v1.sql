-- =============================================================================
-- dim_customers_v1
-- =============================================================================
-- One row per customer. The primary commercial dimension table.
-- Enriches customer attributes with behavioural metrics from order history.
-- PII columns (name, address, phone) are EXCLUDED — this is a public contract.
--
-- Access:   public   — any domain project can ref() this model
-- Contract: enforced — column names and types are pinned
-- Version:  v1       — stable, no breaking changes permitted
--
-- Consumers:
--   dbt_marketing → campaign targeting uses market_segment + is_high_value
--   dbt_analytics → customer KPIs and cohort analysis
-- =============================================================================

with customers as (

    select * from {{ ref('stg_commercial__customers') }}

),

customer_orders as (

    select * from {{ ref('int_customer_orders') }}

),

final as (

    select
        -- primary key
        c.customer_key,

        -- foreign keys
        c.nation_key,

        -- public dimensions (no PII)
        c.market_segment,
        c.account_balance,
        c.is_delinquent,
        c.is_high_value,

        -- behavioural dimensions from order history
        coalesce(co.total_orders, 0)                    as total_orders,
        coalesce(co.lifetime_gross_revenue, 0)          as lifetime_gross_revenue,
        coalesce(co.lifetime_net_revenue, 0)            as lifetime_net_revenue,
        co.first_order_date,
        co.last_order_date,
        coalesce(co.customer_tenure_days, 0)            as customer_tenure_days,
        coalesce(co.total_returns, 0)                   as total_returns,

        -- derived customer tier
        case
            when coalesce(co.lifetime_gross_revenue, 0) >= 500000
                then 'platinum'
            when coalesce(co.lifetime_gross_revenue, 0) >= 100000
                then 'gold'
            when coalesce(co.lifetime_gross_revenue, 0) >= 10000
                then 'silver'
            else 'bronze'
        end                                             as customer_tier,

        -- derived flags
        case
            when co.total_orders is null then true
            else false
        end                                             as is_never_ordered

    from customers c
    left join customer_orders co
        on c.customer_key = co.customer_key

)

select * from final
