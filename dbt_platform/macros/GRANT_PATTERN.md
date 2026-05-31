# Grant Macro Pattern — All Domain Projects
# =============================================================================
# Each dbt project has ONE macro called via on-run-end.
# The macro grants cross-domain read access on that project's schemas.
# Schema names and role names are resolved dynamically from target.* variables.
#
# Pattern:
#   macros/grant_{project}_schemas.sql  →  called by on-run-end in dbt_project.yml
#
# Role resolution:
#   target.name == 'prod'  →  {DOMAIN}_PROD_ROLE
#   target.name == 'dev'   →  {DOMAIN}_DEV_ROLE
#   target.name == 'ci'    →  {DOMAIN}_DEV_ROLE  (CI uses DEV role)
#
# Schema resolution:
#   target.schema = 'kiran'  →  KIRAN_{LAYER}   (dev)
#   target.schema = 'pr_12'  →  PR_12_{LAYER}   (ci)
#   target.schema = ''       →  {LAYER}          (prod)
# =============================================================================
#
# PROJECT          MACRO FILE                        GRANTS
# ─────────────────────────────────────────────────────────────────────────────
# dbt_platform     grant_platform_schemas.sql        STAGING → all 5 domain roles
#
# dbt_commercial   grant_commercial_schemas.sql      MARTS.DIM_CUSTOMERS_V2   → MARKETING
#                                                    MARTS.FCT_DEALS_CLOSED_V2 → FINANCE
#                                                    MARTS (all views)          → ANALYTICS
#
# dbt_finance      grant_finance_schemas.sql         MARTS.FCT_ARR_V1         → ANALYTICS
#                                                    MARTS.FCT_COLLECTIONS_V1 → ANALYTICS
#                                                    REPORTING                 → (none — protected)
#
# dbt_product      grant_product_schemas.sql         MARTS (all views)         → ANALYTICS
#
# dbt_marketing    grant_marketing_schemas.sql       MARTS (all views)         → ANALYTICS
#
# dbt_analytics    (no outbound grants — terminal consumer)
# =============================================================================
