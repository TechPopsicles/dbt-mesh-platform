{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set prefix = env_var('DBT_USER_SCHEMA', '') | upper -%}
    {%- if custom_schema_name is none -%}
        {%- if prefix != '' -%}{{ prefix }}{%- else -%}{{ target.schema }}{%- endif -%}
    {%- elif prefix != '' -%}
        {{ prefix }}_{{ custom_schema_name | trim | upper }}
    {%- else -%}
        {{ custom_schema_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
