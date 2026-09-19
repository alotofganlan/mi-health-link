create or replace function public.get_morning_heart_rate_daily(
    p_start_at timestamptz,
    p_end_at timestamptz,
    p_timezone text
)
returns table (
    day date,
    sample_count bigint,
    avg_bpm double precision,
    min_bpm integer,
    max_bpm integer
)
language sql
stable
security invoker
set search_path = ''
as $$
    select
        (samples.measured_at at time zone p_timezone)::date as day,
        count(*) as sample_count,
        avg(samples.bpm)::double precision as avg_bpm,
        min(samples.bpm)::integer as min_bpm,
        max(samples.bpm)::integer as max_bpm
    from public.heart_rate_samples as samples
    where samples.source = 'xiaomi'
      and samples.measured_at >= p_start_at
      and samples.measured_at <= p_end_at
    group by (samples.measured_at at time zone p_timezone)::date
    order by day;
$$;

revoke all on function public.get_morning_heart_rate_daily(
    timestamptz, timestamptz, text
) from public, anon, authenticated;

grant execute on function public.get_morning_heart_rate_daily(
    timestamptz, timestamptz, text
) to service_role;
