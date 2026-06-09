-- Reads Commercial's public contract — not raw source data
with source as (
    select * from {{ source('commercial', 'fct_orders_v1') }}
),

enriched as (
    select
        order_key,
        customer_key,
        order_date,
        order_status,
        order_status_label,
        gross_revenue,
        net_revenue,
        extended_revenue,
        line_item_count,
        -- Finance-specific derivations
        gross_revenue - net_revenue                 as total_discount_amount,
        case
            when gross_revenue > 0
            then round((gross_revenue - net_revenue) / gross_revenue, 4)
            else 0
        end                                         as effective_discount_rate,
        date_trunc('month', order_date)             as order_month,
        date_trunc('quarter', order_date)           as order_quarter,
        extract('year', order_date)                 as order_year
    from source
)

select * from enriched
