{% macro safe_divide(numerator, denominator, default=0) %}
    case
        when {{ denominator }} = 0 or {{ denominator }} is null
            then {{ default }}
        else {{ numerator }} / {{ denominator }}
    end
{% endmacro %}
