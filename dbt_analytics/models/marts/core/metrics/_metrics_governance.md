# Metrics Governance — dbt_analytics

## The problem this solves

Before this metric registry existed, five teams had five definitions of revenue:

| Team | Model | Column | Includes tax? | Includes discount? |
|---|---|---|---|---|
| Commercial | fct_orders_v1 | gross_revenue | ✅ | ❌ |
| Finance | fct_revenue_v1 | net_revenue | ❌ | ✅ |
| Analytics (old) | custom_query | total_rev | ✅ | ✅ |
| Marketing | fct_attribution_v1 | segment_revenue | ✅ | ❌ |
| Product | fct_usage_v1 | usage_value | N/A | N/A |

The CFO asked "what was last month's revenue?" and got three different numbers
depending on who they asked. Every Monday morning started with reconciliation.

## The solution

One metric registry. Every number flows through here.
Teams query metrics, never raw columns.

## Metric ownership

| Metric | Owner | Source model | Notes |
|---|---|---|---|
| `gross_revenue` | Analytics | fct_orders_enriched | THE number. Includes tax. |
| `net_revenue` | Analytics | fct_orders_enriched | After discount, before tax. |
| `total_discount` | Analytics | fct_orders_enriched | Gross - net. |
| `order_count` | Analytics | fct_orders_enriched | Distinct orders. |
| `avg_order_value` | Analytics | fct_orders_enriched | AOV = gross / orders. |
| `discount_rate` | Finance | fct_orders_enriched | Flag if > 10% in any segment. |
| `active_customers` | Analytics | fct_orders_enriched | Has placed >= 1 order. |
| `revenue_per_customer` | Analytics | fct_orders_enriched | LTV proxy. |

## Rules for adding metrics

1. **Never duplicate a concept.** If `gross_revenue` exists, don't add
   `total_sales` or `revenue`. Extend the existing metric.

2. **Always document the owner.** Every metric needs an owner team
   in the description. Ownerless metrics get deleted in quarterly cleanup.

3. **Define what it excludes.** Revenue metrics must state explicitly
   whether they include tax, discounts, returns. Ambiguity is the enemy.

4. **Breaking changes require announcement.** Changing a metric definition
   is a breaking change to every dashboard that uses it. Open a PR,
   post in #data-platform Slack, give 2-week notice.

5. **Semantic models are contracts.** Renaming a measure in a semantic model
   breaks every metric that references it. Version semantic models like marts.

## Querying metrics

```bash
# From dbt_analytics/
dbt sl query --metrics gross_revenue --group-by order_month
dbt sl query --metrics gross_revenue,net_revenue --group-by market_segment
dbt sl query --metrics avg_order_value --group-by customer_tier,order_month
dbt sl query --metrics active_customers --group-by market_segment
```

## Adding a new metric — checklist

- [ ] Does this metric already exist under a different name?
- [ ] Which semantic model does it read from?
- [ ] What is the grain?
- [ ] Who owns it?
- [ ] What does it explicitly exclude?
- [ ] Has the Analytics team reviewed the PR?
