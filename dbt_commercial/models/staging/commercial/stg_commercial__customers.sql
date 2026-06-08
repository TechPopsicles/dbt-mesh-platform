with source as (

    select * from {{ source('platform', 'stg_tpch__customer') }}

),

enriched as (

    select
        -- primary key
        custkey                                             as customer_key,

        -- foreign keys
        nationkey                                           as nation_key,

        -- dimensions (PII — excluded from public marts)
        name                                                as customer_name,
        address                                             as customer_address,
        phone                                               as customer_phone,
        mktsegment                                          as market_segment,

        -- measures
        acctbal                                             as account_balance,

        -- derived flags
        case
            when acctbal < 0 then true
            else false
        end                                                 as is_delinquent,

        case
            when acctbal >= 5000 then true
            else false
        end                                                 as is_high_value,

        -- metadata
        comment                                             as customer_comment

    from source

)

select * from enriched
