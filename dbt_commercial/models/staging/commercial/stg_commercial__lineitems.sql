with source as (

    select * from {{ source('platform', 'stg_tpch__lineitem') }}

),

enriched as (

    select
        -- composite key
        {{ dbt_utils.generate_surrogate_key(['orderkey', 'linenumber']) }}
                                                            as lineitem_key,

        -- foreign keys
        orderkey                                            as order_key,

        -- dimensions
        linenumber                                          as line_number,
        returnflag                                          as return_flag,
        linestatus                                          as line_status,
        shipmode                                            as ship_mode,

        -- measures (raw)
        quantity,
        extendedprice                                       as extended_price,
        discount                                            as discount_rate,
        tax                                                 as tax_rate,

        -- derived revenue measures
        round(extendedprice * (1 - discount), 2)           as net_price,
        round(extendedprice * (1 - discount)
              * (1 + tax), 2)                              as gross_price,

        -- dates
        cast(shipdate as date)                             as ship_date,
        cast(commitdate as date)                           as commit_date,
        cast(receiptdate as date)                          as receipt_date

    from source

)

select * from enriched
