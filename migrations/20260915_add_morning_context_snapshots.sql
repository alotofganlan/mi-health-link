create table if not exists public.morning_context_snapshots (
    report_id uuid primary key
        references public.wake_report_deliveries(id) on delete cascade,
    schema_version text not null,
    source_versions jsonb not null default '{}'::jsonb,
    payload jsonb not null,
    snapshot_mode text not null check (
        snapshot_mode in ('native', 'legacy_hydrated')
    ),
    generated_at timestamptz not null default now()
);

alter table public.morning_context_snapshots enable row level security;

revoke all on table public.morning_context_snapshots
    from anon, authenticated, service_role;

grant select, insert on table public.morning_context_snapshots to service_role;
