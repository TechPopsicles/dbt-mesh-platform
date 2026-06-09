-- =============================================================================
-- fct_revenue_v1 — Finance public contract
-- =============================================================================
-- Monthly revenue aggregation. One row per (customer, month).
-- Published as a public contract consumed by dbt_analytics.
-- Grain: customer_key + order_month
-- =============================================================================

with orders as (
    select * from {{ ref('stg_finance__orders') }}
),

monthly_revenue as (
    select
        customer_key,
        order_month,
        order_quarter,
        order_year,

        -- revenue measures
        sum(gross_revenue)                          as gross_revenue,
        sum(net_revenue)                            as net_revenue,
        sum(total_discount_amount)                  as total_discount_amount,
        count(distinct order_key)                   as order_count,
        sum(line_item_count)                        as total_line_items,

        -- derived
        round(avg(effective_discount_rate), 4)      as avg_discount_rate,
        round(sum(gross_revenue)
              / nullif(count(distinct order_key), 0), 2)
                                                    as avg_order_value

    from orders
    group by
        customer_key,
        order_month,
        order_quarter,
        order_year
)

select * from monthly_revenue
