-- Line items as proxy for product feature usage
-- In a real system this would be event stream data (Segment, Snowplow, etc.)
with source as (
    select * from {{ source('platform', 'stg_tpch__lineitem') }}
),

enriched as (
    select
        {{ dbt_utils.generate_surrogate_key(['orderkey', 'linenumber']) }}
                                                    as usage_event_key,
        orderkey                                    as order_key,
        linenumber                                  as feature_id,
        shipmode                                    as feature_category,
        quantity                                    as usage_quantity,
        extendedprice                               as usage_value,
        cast(shipdate as date)                      as usage_date,
        date_trunc('month', cast(shipdate as date)) as usage_month,
        returnflag                                  as usage_status,
        case
            when returnflag = 'R' then true
            else false
        end                                         as is_churned
    from source
    where shipdate is not null
)

select * from enriched
