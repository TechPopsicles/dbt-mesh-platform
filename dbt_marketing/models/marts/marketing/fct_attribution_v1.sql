-- =============================================================================
-- fct_attribution_v1 — Marketing public contract
-- =============================================================================
-- Customer acquisition and engagement metrics by segment.
-- Proxy for campaign attribution using TPC-H customer + order data.
-- One row per (market_segment, customer_tier, nation_key).
-- Grain: market_segment + customer_tier + nation_key
-- =============================================================================

with customers as (
    select * from {{ ref('stg_marketing__customers') }}
),

attribution as (
    select
        market_segment,
        customer_tier,
        nation_key,
        frequency_segment,
        monetary_segment,

        count(customer_key)                         as customer_count,
        sum(lifetime_gross_revenue)                 as segment_revenue,
        avg(lifetime_gross_revenue)                 as avg_customer_revenue,
        sum(total_orders)                           as segment_order_count,
        avg(total_orders)                           as avg_orders_per_customer,
        sum(case when is_high_value then 1
                 else 0 end)                        as high_value_count,
        sum(case when is_never_ordered then 1
                 else 0 end)                        as never_ordered_count,
        sum(case when is_delinquent then 1
                 else 0 end)                        as delinquent_count,
        avg(customer_tenure_days)                   as avg_tenure_days

    from customers
    group by
        market_segment,
        customer_tier,
        nation_key,
        frequency_segment,
        monetary_segment
)

select * from attribution
