{# 
  Wrapper around dbt_utils.generate_surrogate_key that enforces
  consistent null-handling across all platform models.
  Usage: {{ platform_surrogate_key(['col1', 'col2']) }}
#}
{% macro platform_surrogate_key(field_list) %}
    {{ dbt_utils.generate_surrogate_key(field_list) }}
{% endmacro %}
