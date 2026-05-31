with source as (

    select * from {{ source('tpch', 'PARTSUPP') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['ps_partkey', 'ps_suppkey']) }}  as partsupp_key,
        PS_PARTKEY                                           as partkey,
        PS_SUPPKEY                                           as suppkey,
        PS_AVAILQTY                                          as availqty,
        PS_SUPPLYCOST                                        as supplycost,
        PS_COMMENT                                           as comment

    from source

)

select * from renamed
