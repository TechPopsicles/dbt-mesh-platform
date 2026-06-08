-- =============================================================================
-- fct_orders_v1
-- =============================================================================
-- One row per order. The primary commercial fact table.
-- Aggregates line-item revenue to the order level.
--
-- Access:  public   — any domain project can ref() this model
-- Contract: enforced — column names and types are pinned
-- Version:  v1      — stable, no breaking changes permitted
--                     breaking changes → bump to v2 + deprecate v1
--
-- Consumers:
--   dbt_finance   → CAC calculation uses gross_revenue
--   dbt_analytics → GTM funnel and executive KPI marts
-- =============================================================================

with order_items as (

    select * from {{ ref('int_order_items') }}

),

orders as (

    select * from {{ ref('stg_commercial__orders') }}

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

        -- revenue (aggregated from line items)
        sum(oi.gross_price)                             as gross_revenue,
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
