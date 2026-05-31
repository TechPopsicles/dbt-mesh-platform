with source as (

    select * from {{ source('tpch', 'NATION') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['n_nationkey', 'n_name']) }}  as nation_key,
        N_NATIONKEY                                          as nationkey,
        N_NAME                                               as name,
        N_REGIONKEY                                          as regionkey,
        N_COMMENT                                            as comment

    from source

)

select * from renamed
