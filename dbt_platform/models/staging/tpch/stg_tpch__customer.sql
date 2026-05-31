with source as (

    select * from {{ source('tpch', 'CUSTOMER') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['c_custkey', 'c_name']) }}  as customer_key,
        C_CUSTKEY                                            as custkey,
        C_NAME                                               as name,
        C_ADDRESS                                            as address,
        C_NATIONKEY                                          as nationkey,
        C_PHONE                                              as phone,
        C_ACCTBAL                                            as acctbal,
        C_MKTSEGMENT                                         as mktsegment,
        C_COMMENT                                            as comment

    from source

)

select * from renamed
