with source as (

    select * from {{ source('tpch', 'REGION') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['r_regionkey', 'r_name']) }}  as region_key,
        R_REGIONKEY                                          as regionkey,
        R_NAME                                               as name,
        R_COMMENT                                            as comment

    from source

)

select * from renamed
