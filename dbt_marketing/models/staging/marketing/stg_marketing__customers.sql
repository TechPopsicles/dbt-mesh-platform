-- Reads Commercial's public contract — no PII exposed
with source as (
    select * from {{ source('commercial', 'dim_customers_v1') }}
),

enriched as (
    select
        customer_key,
        nation_key,
        market_segment,
        account_balance,
        is_high_value,
        is_delinquent,
        customer_tier,
        lifetime_gross_revenue,
        total_orders,
        first_order_date,
        last_order_date,
        customer_tenure_days,
        total_returns,
        is_never_ordered,

        -- RFM scoring proxy
        case
            when total_orders >= 10 then 'high'
            when total_orders >= 3  then 'medium'
            else 'low'
        end                                         as frequency_segment,

        case
            when lifetime_gross_revenue >= 500000 then 'high'
            when lifetime_gross_revenue >= 50000  then 'medium'
            else 'low'
        end                                         as monetary_segment

    from source
)

select * from enriched
