with source as (

    select * from {{ source('tpch', 'LINEITEM') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['l_orderkey', 'l_partkey']) }}  as lineitem_key,
        L_ORDERKEY                                           as orderkey,
        L_PARTKEY                                            as partkey,
        L_SUPPKEY                                            as suppkey,
        L_LINENUMBER                                         as linenumber,
        L_QUANTITY                                           as quantity,
        L_EXTENDEDPRICE                                      as extendedprice,
        L_DISCOUNT                                           as discount,
        L_TAX                                                as tax,
        L_RETURNFLAG                                         as returnflag,
        L_LINESTATUS                                         as linestatus,
        cast(L_SHIPDATE as date)                                as shipdate,
        cast(L_COMMITDATE as date)                              as commitdate,
        cast(L_RECEIPTDATE as date)                             as receiptdate,
        L_SHIPINSTRUCT                                       as shipinstruct,
        L_SHIPMODE                                           as shipmode,
        L_COMMENT                                            as comment

    from source

)

select * from renamed
