-- =============================================================================
-- fct_orders_v2
-- =============================================================================
-- Breaking change from v1: gross_revenue renamed to total_revenue
-- to align with the company-wide metric glossary.
--
-- Migration guide for v1 consumers:
--   - Replace fct_orders_v1.gross_revenue with fct_orders_v2.total_revenue
--   - All other columns are identical to v1
--   - v1 deprecated 2026-07-08 — migrate before that date
-- =============================================================================

with order_items as (

    select * from {{ ref('int_order_items') }}

),

aggregated as (

    select
        -- primary key
        oi.order_key,

        -- foreign keys
        oi.customer_key,

        -- order dimensions
        oi.order_date,
        oi.order_status,
        oi.order_status_label,
        oi.order_priority,
        oi.is_high_priority,

        -- revenue — total_revenue replaces gross_revenue from v1
        sum(oi.gross_price)                             as total_revenue,
        sum(oi.net_price)                               as net_revenue,
        sum(oi.extended_price)                          as extended_revenue,
        sum(oi.quantity)                                as total_quantity,
        count(oi.lineitem_key)                          as line_item_count,

        -- discount summary
        avg(oi.discount_rate)                           as avg_discount_rate,

        -- fulfillment metrics
        min(oi.ship_date)                               as first_ship_date,
        max(oi.receipt_date)                            as last_receipt_date,
        sum(case when oi.is_returned then 1
                 else 0 end)                            as returned_lines,
        sum(case when oi.is_late_delivery then 1
                 else 0 end)                            as late_lines

    from order_items oi
    group by
        oi.order_key,
        oi.customer_key,
        oi.order_date,
        oi.order_status,
        oi.order_status_label,
        oi.order_priority,
        oi.is_high_priority

)

select * from aggregated
