create table if not exists public.xiaomi_coverage_ranges (
    source text not null default 'xiaomi',
    key text not null,
    range_start timestamptz not null,
    range_end timestamptz not null,
    checked_at timestamptz not null default now(),
    status text not null check (status in ('success', 'failed')),
    primary key (source, key, range_start, range_end),
    check (range_end >= range_start)
);

create index if not exists xiaomi_coverage_ranges_lookup_idx
    on public.xiaomi_coverage_ranges (source, key, range_start, range_end);
