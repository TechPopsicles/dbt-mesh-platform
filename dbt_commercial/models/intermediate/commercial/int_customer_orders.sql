-- =============================================================================
-- int_customer_orders
-- =============================================================================
-- Aggregates order history per customer. Used by dim_customers to add
-- behavioural metrics to the customer dimension.
-- Grain: customer_key (one row per customer).
-- Access: private — intermediate model, never cross-domain referenced.
-- =============================================================================

with order_items as (

    select * from {{ ref('int_order_items') }}

),

customer_metrics as (

    select
        customer_key,

        -- order volume
        count(distinct order_key)                       as total_orders,
        count(lineitem_key)                             as total_line_items,
        sum(quantity)                                   as total_quantity,

        -- revenue
        sum(gross_price)                                as lifetime_gross_revenue,
        sum(net_price)                                  as lifetime_net_revenue,
        avg(gross_price)                                as avg_line_gross_revenue,

        -- order dates
        min(order_date)                                 as first_order_date,
        max(order_date)                                 as last_order_date,
        datediff('day', min(order_date),
                 max(order_date))                       as customer_tenure_days,

        -- order quality
        sum(case when is_returned then 1 else 0 end)    as total_returns,
        sum(case when is_late_delivery then 1
                 else 0 end)                            as total_late_deliveries,
        sum(case when order_status_label = 'fulfilled'
                 then 1 else 0 end)                     as fulfilled_orders,

        -- priority behaviour
        sum(case when is_high_priority then 1
                 else 0 end)                            as high_priority_orders

    from order_items
    group by customer_key

)

select * from customer_metrics
