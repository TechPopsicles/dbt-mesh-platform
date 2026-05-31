with source as (

    select * from {{ source('tpch', 'PART') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key(['p_partkey', 'p_name']) }}  as part_key,
        P_PARTKEY                                            as partkey,
        P_NAME                                               as name,
        P_MFGR                                               as mfgr,
        P_BRAND                                              as brand,
        P_TYPE                                               as type,
        P_SIZE                                               as size,
        P_CONTAINER                                          as container,
        P_RETAILPRICE                                        as retailprice,
        P_COMMENT                                            as comment

    from source

)

select * from renamed
