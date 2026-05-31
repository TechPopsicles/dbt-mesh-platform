with source as (

    select * from {{ source('tpch', 'SUPPLIER') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['s_suppkey', 's_name']) }}  as supplier_key,
        S_SUPPKEY                                            as suppkey,
        S_NAME                                               as name,
        S_ADDRESS                                            as address,
        S_NATIONKEY                                          as nationkey,
        S_PHONE                                              as phone,
        S_ACCTBAL                                            as acctbal,
        S_COMMENT                                            as comment

    from source

)

select * from renamed
