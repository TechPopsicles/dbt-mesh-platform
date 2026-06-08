-- Demo model simulating a downstream consumer still on v1
-- This triggers the deprecation warning: 
-- [WARNING]: While compiling '_demo_v1_consumer': Found a reference to fct_orders.v1, which is slated for deprecation on '2026-07-08T00:00:00-04:00
select * from {{ ref('fct_orders', v=1) }}