with source as (

    select * from {{ source('tpch', 'ORDERS') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['o_orderkey', 'o_custkey']) }}  as orders_key,
        O_ORDERKEY                                           as orderkey,
        O_CUSTKEY                                            as custkey,
        O_ORDERSTATUS                                        as orderstatus,
        O_TOTALPRICE                                         as totalprice,
        cast(O_ORDERDATE as date)                               as orderdate,
        O_ORDERPRIORITY                                      as orderpriority,
        O_CLERK                                              as clerk,
        O_SHIPPRIORITY                                       as shippriority,
        O_COMMENT                                            as comment

    from source

)

select * from renamed
