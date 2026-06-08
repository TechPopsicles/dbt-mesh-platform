with source as (

    -- Reads from Platform staging via source() — the dbt Mesh boundary.
    -- Commercial never touches SNOWFLAKE_SAMPLE_DATA directly.
    select * from {{ source('platform', 'stg_tpch__orders') }}

),

enriched as (

    select
        -- primary key
        orderkey                                            as order_key,

        -- foreign keys
        custkey                                             as customer_key,

        -- dimensions
        orderstatus                                         as order_status,
        orderpriority                                       as order_priority,
        clerk                                               as clerk_name,
        shippriority                                        as ship_priority,

        -- measures
        totalprice                                          as order_total_price,

        -- dates
        cast(orderdate as date)                             as order_date,

        -- derived dimensions
        case
            when orderpriority in ('1-URGENT', '2-HIGH')
                then true
            else false
        end                                                 as is_high_priority,

        case
            when orderstatus = 'F' then 'fulfilled'
            when orderstatus = 'O' then 'open'
            when orderstatus = 'P' then 'partial'
            else 'unknown'
        end                                                 as order_status_label,

        -- metadata
        comment                                             as order_comment

    from source

)

select * from enriched
