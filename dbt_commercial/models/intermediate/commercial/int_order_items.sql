-- =============================================================================
-- int_order_items
-- =============================================================================
-- Joins order headers with line items to produce a complete revenue picture
-- at the line-item grain. Used by fct_orders to aggregate to order level.
-- Grain: lineitem_key (one row per order line).
-- Access: private — intermediate model, never cross-domain referenced.
-- =============================================================================

with orders as (

    select
        order_key,
        customer_key,
        order_date,
        order_status,
        order_status_label,
        order_priority,
        is_high_priority,
        clerk_name

    from {{ ref('stg_commercial__orders') }}

),

lineitems as (

    select
        lineitem_key,
        order_key,
        line_number,
        quantity,
        extended_price,
        discount_rate,
        tax_rate,
        net_price,
        gross_price,
        return_flag,
        line_status,
        ship_mode,
        ship_date,
        commit_date,
        receipt_date

    from {{ ref('stg_commercial__lineitems') }}

),

joined as (

    select
        -- keys
        l.lineitem_key,
        o.order_key,
        o.customer_key,

        -- order dimensions
        o.order_date,
        o.order_status,
        o.order_status_label,
        o.order_priority,
        o.is_high_priority,
        o.clerk_name,

        -- line dimensions
        l.line_number,
        l.return_flag,
        l.line_status,
        l.ship_mode,

        -- dates
        l.ship_date,
        l.commit_date,
        l.receipt_date,

        -- derived date metrics
        datediff('day', o.order_date, l.ship_date)      as days_to_ship,
        datediff('day', l.ship_date, l.receipt_date)    as days_in_transit,
        datediff('day', l.commit_date, l.receipt_date)  as days_vs_commit,

        -- revenue measures
        l.quantity,
        l.extended_price,
        l.discount_rate,
        l.tax_rate,
        l.net_price,
        l.gross_price,

        -- flags
        case
            when l.return_flag = 'R' then true
            else false
        end                                              as is_returned,

        case
            when l.receipt_date > l.commit_date then true
            else false
        end                                              as is_late_delivery

    from orders o
    inner join lineitems l
        on o.order_key = l.order_key

)

select * from joined
