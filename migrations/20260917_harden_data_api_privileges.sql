begin;

-- Keep all health and report tables private to the server-side service role.

alter table public.raw_records enable row level security;
alter table public.health_records enable row level security;
alter table public.heart_rate_samples enable row level security;
alter table public.spo2_samples enable row level security;
alter table public.intensity_samples enable row level security;
alter table public.body_measurements enable row level security;
alter table public.menstrual_records enable row level security;
alter table public.sleep_sessions enable row level security;
alter table public.sleep_stages enable row level security;
alter table public.workouts enable row level security;
alter table public.xiaomi_sync_state enable row level security;
alter table public.xiaomi_sync_jobs enable row level security;
alter table public.glucose_samples enable row level security;
alter table public.xiaomi_coverage_ranges enable row level security;
alter table public.diet_meals enable row level security;
alter table public.diet_food_items enable row level security;
alter table public.device_presence enable row level security;
alter table public.wake_report_deliveries enable row level security;
alter table public.device_wake_state enable row level security;
alter table public.morning_context_snapshots enable row level security;

revoke all on table public.raw_records from anon, authenticated;
revoke all on table public.health_records from anon, authenticated;
revoke all on table public.heart_rate_samples from anon, authenticated;
revoke all on table public.spo2_samples from anon, authenticated;
revoke all on table public.intensity_samples from anon, authenticated;
revoke all on table public.body_measurements from anon, authenticated;
revoke all on table public.menstrual_records from anon, authenticated;
revoke all on table public.sleep_sessions from anon, authenticated;
revoke all on table public.sleep_stages from anon, authenticated;
revoke all on table public.workouts from anon, authenticated;
revoke all on table public.xiaomi_sync_state from anon, authenticated;
revoke all on table public.xiaomi_sync_jobs from anon, authenticated;
revoke all on table public.glucose_samples from anon, authenticated;
revoke all on table public.xiaomi_coverage_ranges from anon, authenticated;
revoke all on table public.diet_meals from anon, authenticated;
revoke all on table public.diet_food_items from anon, authenticated;
revoke all on table public.device_presence from anon, authenticated;
revoke all on table public.wake_report_deliveries from anon, authenticated;
revoke all on table public.device_wake_state from anon, authenticated;
revoke all on table public.morning_context_snapshots from anon, authenticated;

grant select, insert, update, delete on table public.raw_records to service_role;
grant select, insert, update, delete on table public.health_records to service_role;
grant select, insert, update, delete on table public.heart_rate_samples to service_role;
grant select, insert, update, delete on table public.spo2_samples to service_role;
grant select, insert, update, delete on table public.intensity_samples to service_role;
grant select, insert, update, delete on table public.body_measurements to service_role;
grant select, insert, update, delete on table public.menstrual_records to service_role;
grant select, insert, update, delete on table public.sleep_sessions to service_role;
grant select, insert, update, delete on table public.sleep_stages to service_role;
grant select, insert, update, delete on table public.workouts to service_role;
grant select, insert, update, delete on table public.xiaomi_sync_state to service_role;
grant select, insert, update, delete on table public.xiaomi_sync_jobs to service_role;
grant select, insert, update, delete on table public.glucose_samples to service_role;
grant select, insert, update, delete on table public.xiaomi_coverage_ranges to service_role;
grant select, insert, update, delete on table public.diet_meals to service_role;
grant select, insert, update, delete on table public.diet_food_items to service_role;
grant select, insert, update, delete on table public.device_presence to service_role;
grant select, insert, update, delete on table public.wake_report_deliveries to service_role;
grant select, insert, update, delete on table public.device_wake_state to service_role;

revoke all on table public.morning_context_snapshots from service_role;
grant select, insert on table public.morning_context_snapshots to service_role;

do $$
declare
    sequence_name text;
begin
    foreach sequence_name in array array[
        'raw_records_id_seq',
        'health_records_id_seq',
        'heart_rate_samples_id_seq',
        'spo2_samples_id_seq',
        'intensity_samples_id_seq',
        'body_measurements_id_seq',
        'menstrual_records_id_seq',
        'sleep_sessions_id_seq',
        'sleep_stages_id_seq',
        'workouts_id_seq',
        'glucose_samples_id_seq',
        'diet_meals_id_seq',
        'diet_food_items_id_seq',
        'device_presence_id_seq'
    ]
    loop
        if to_regclass('public.' || sequence_name) is not null then
            execute format(
                'revoke all on sequence public.%I from anon, authenticated',
                sequence_name
            );
            execute format(
                'grant usage, select on sequence public.%I to service_role',
                sequence_name
            );
        end if;
    end loop;
end
$$;

commit;
