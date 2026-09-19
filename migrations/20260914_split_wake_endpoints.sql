begin;

-- Split location updates from unlock probes and deduplicate by stable sleep id.

alter table public.device_presence
    drop constraint if exists device_presence_event_check;

alter table public.device_presence
    add constraint device_presence_event_check
    check (event in ('unlock', 'location_update'));

create table if not exists public.device_wake_state (
    device text primary key,
    last_unlock_at timestamptz not null,
    source text not null default 'automate_unlock'
        check (source = 'automate_unlock'),
    updated_at timestamptz not null default now()
);

alter table public.device_wake_state enable row level security;
revoke all on table public.device_wake_state from anon, authenticated;
grant select, insert, update, delete on table public.device_wake_state to service_role;

create or replace function public.keep_latest_device_unlock()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.last_unlock_at := greatest(old.last_unlock_at, new.last_unlock_at);
    new.updated_at := now();
    return new;
end;
$$;

revoke all on function public.keep_latest_device_unlock() from public;

drop trigger if exists device_wake_state_keep_latest on public.device_wake_state;
create trigger device_wake_state_keep_latest
before update on public.device_wake_state
for each row execute function public.keep_latest_device_unlock();

alter table public.wake_report_deliveries
    drop constraint if exists wake_report_deliveries_report_kind_check;

alter table public.wake_report_deliveries
    add constraint wake_report_deliveries_report_kind_check
    check (report_kind in ('morning', 'nap', 'sleep_update', 'new_sleep'));

with ranked_deliveries as (
    select id, row_number() over (
        partition by device, sleep_source_record_id
        order by
            (status = 'completed') desc,
            coalesce(triggered_at, claimed_at, created_at) desc,
            id desc
    ) as row_number
    from public.wake_report_deliveries
)
delete from public.wake_report_deliveries as delivery
using ranked_deliveries as ranked
where delivery.id = ranked.id
  and ranked.row_number > 1;

alter table public.wake_report_deliveries
    drop constraint if exists wake_report_deliveries_device_sleep_fingerprint_key;

alter table public.wake_report_deliveries
    add constraint wake_report_deliveries_device_sleep_source_record_id_key
    unique (device, sleep_source_record_id);

commit;
