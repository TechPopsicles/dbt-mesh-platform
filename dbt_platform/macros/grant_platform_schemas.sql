-- =============================================================================
-- macros/grant_platform_schemas.sql
-- =============================================================================
-- Grants cross-domain read access on Platform schemas after every dbt build.
-- Called automatically via on-run-end hook in dbt_project.yml.
--
-- Schema naming mirrors generate_schema_name macro:
--   Dev   target.schema = 'kiran'  →  KIRAN_STAGING
--   CI    target.schema = 'pr_12'  →  PR_12_STAGING
--   Prod  target.schema = ''       →  STAGING
--
-- Role naming mirrors Snowflake setup:
--   Dev/CI  target.name = 'dev' or 'ci'  →  COMMERCIAL_DEV_ROLE
--   Prod    target.name = 'prod'          →  COMMERCIAL_PROD_ROLE
--
-- Business rule:
--   Platform staging  →  all 5 domain roles  (read-only on views)
--   Platform intermediate + marts  →  not granted cross-domain
-- =============================================================================

{% macro grant_platform_schemas() %}

    {# ── Resolve environment ──────────────────────────────────────────────── #}

    {%- set db = target.database -%}

    {%- set schema_prefix = (target.schema | upper ~ '_')
        if target.schema | trim != ''
        else '' -%}

    {%- set staging_schema = schema_prefix ~ 'STAGING' -%}

    {# CI and dev both use DEV roles — prod uses PROD roles #}
    {%- set role_env = 'PROD'
        if target.name == 'prod'
        else 'DEV' -%}

    {# Roles that read Platform staging #}
    {%- set reader_roles = [
        'COMMERCIAL_' ~ role_env ~ '_ROLE',
        'FINANCE_'    ~ role_env ~ '_ROLE',
        'PRODUCT_'    ~ role_env ~ '_ROLE',
        'MARKETING_'  ~ role_env ~ '_ROLE',
        'ANALYTICS_'  ~ role_env ~ '_ROLE',
    ] -%}

    {# ── Log what we are about to grant ──────────────────────────────────── #}
    {{ log("grant_platform_schemas: target=" ~ target.name
           ~ " db=" ~ db
           ~ " schema=" ~ staging_schema
           ~ " roles=" ~ reader_roles | join(', '), info=true) }}

    {# ── Execute grants ───────────────────────────────────────────────────── #}
    {%- for role in reader_roles %}

        {# USAGE on schema #}
        {% call statement('grant_usage_' ~ loop.index, fetch_result=false) %}
            GRANT USAGE
                ON SCHEMA {{ db }}.{{ staging_schema }}
                TO ROLE {{ role }}
        {% endcall %}

        {# SELECT on all existing views #}
        {% call statement('grant_select_all_' ~ loop.index, fetch_result=false) %}
            GRANT SELECT ON ALL VIEWS
                IN SCHEMA {{ db }}.{{ staging_schema }}
                TO ROLE {{ role }}
        {% endcall %}

        {# SELECT on future views — covers models added after this run #}
        {% call statement('grant_select_future_' ~ loop.index, fetch_result=false) %}
            GRANT SELECT ON FUTURE VIEWS
                IN SCHEMA {{ db }}.{{ staging_schema }}
                TO ROLE {{ role }}
        {% endcall %}

        {{ log("  granted → " ~ role, info=true) }}

    {%- endfor %}

    {{ log("grant_platform_schemas: done ✓", info=true) }}

{% endmacro %}
