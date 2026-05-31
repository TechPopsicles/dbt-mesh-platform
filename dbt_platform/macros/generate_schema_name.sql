-- =============================================================================
-- generate_schema_name.sql
-- =============================================================================
-- Overrides dbt's default schema naming to support three environments cleanly.
--
-- Environment behaviour:
--   dev   →  {username}_staging   e.g.  kiran_staging
--   ci    →  pr_{n}_staging       e.g.  pr_12_staging
--   prod  →  staging              (no prefix — database carries env context)
--
-- Controlled by DBT_USER_SCHEMA environment variable:
--   dev   →  export DBT_USER_SCHEMA=kiran        (set in ~/.zshrc or ~/.bashrc)
--   ci    →  DBT_USER_SCHEMA=pr_12               (set by GitHub Actions workflow)
--   prod  →  DBT_USER_SCHEMA=                    (empty string — no prefix)
--
-- Schema produced:
--   custom_schema_name present + prefix present  →  {prefix}_{custom_schema}
--   custom_schema_name present + prefix absent   →  {custom_schema}            (prod)
--   custom_schema_name absent  + prefix present  →  {prefix}                   (fallback)
--   custom_schema_name absent  + prefix absent   →  {target.schema}            (safety net)
-- =============================================================================

{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set prefix = env_var('DBT_USER_SCHEMA', '') | trim -%}

    {%- if custom_schema_name is none -%}

        {%- if prefix != '' -%}
            {{ prefix }}
        {%- else -%}
            {{ target.schema }}
        {%- endif -%}

    {%- elif prefix != '' -%}
        {{ prefix }}_{{ custom_schema_name | trim }}

    {%- else -%}
        {{ custom_schema_name | trim }}

    {%- endif -%}

{%- endmacro %}
