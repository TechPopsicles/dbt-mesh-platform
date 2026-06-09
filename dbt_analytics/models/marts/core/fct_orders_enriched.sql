-- =============================================================================
-- fct_orders_enriched — Analytics cross-domain mart
-- =============================================================================
-- Joins Commercial orders with Finance revenue metrics and
-- customer dimension for executive-level reporting.
-- This is the "one truth" model — authoritative numbers for all teams.
-- Grain: order_key
-- =============================================================================

with orders as (
    select * from {{ source('commercial', 'fct_orders_v1') }}
),

customers as (
    select * from {{ source('commercial', 'dim_customers_v1') }}
),

enriched as (
    select
        -- order keys
        o.order_key,
        o.customer_key,

        -- order dimensions
        o.order_date,
        o.order_status,
        o.order_status_label,
        o.order_priority,
        o.is_high_priority,

        -- customer dimensions (no PII — public contract only)
        c.market_segment,
        c.customer_tier,
        c.nation_key,
        c.is_high_value,
        c.is_delinquent,

        -- revenue measures — THE authoritative numbers
        o.gross_revenue,
        o.net_revenue,
        o.line_item_count,
        o.avg_discount_rate,

        -- date dimensions
        date_trunc('month', o.order_date)           as order_month,
        date_trunc('quarter', o.order_date)         as order_quarter,
        extract('year', o.order_date)               as order_year

    from orders o
    left join customers c
        on o.customer_key = c.customer_key
)

select * from enriched
