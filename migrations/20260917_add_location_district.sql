begin;

alter table public.device_presence
    add column if not exists district text;

commit;
