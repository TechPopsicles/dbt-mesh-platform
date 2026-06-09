-- =============================================================================
-- fct_usage_v1 — Product public contract
-- =============================================================================
-- Monthly feature usage aggregation. Proxy for product analytics
-- using TPC-H lineitem data. One row per (feature_id, usage_month).
-- Grain: feature_id + usage_month
-- =============================================================================

with usage as (
    select * from {{ ref('stg_product__usage') }}
),

monthly_usage as (
    select
        feature_id,
        feature_category,
        usage_month,

        count(usage_event_key)                      as total_events,
        count(distinct order_key)                   as unique_users,
        sum(usage_quantity)                         as total_quantity,
        sum(usage_value)                            as total_usage_value,
        sum(case when is_churned then 1 else 0 end) as churned_events,
        round(
            sum(case when is_churned then 1 else 0 end)
            / nullif(count(usage_event_key), 0), 4
        )                                           as churn_rate

    from usage
    group by feature_id, feature_category, usage_month
)

select * from monthly_usage
