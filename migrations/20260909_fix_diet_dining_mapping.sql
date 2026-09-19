-- Align normalized Xiaomi diet meal types with the six dining codes used by the app.

alter table public.diet_meals
    drop constraint if exists diet_meals_meal_type_check;

alter table public.diet_meals
    add constraint diet_meals_meal_type_check
    check (
        meal_type in (
            'breakfast',
            'morning_snack',
            'lunch',
            'afternoon_snack',
            'dinner',
            'evening_snack',
            'other'
        )
    );

update public.diet_meals
set meal_type = case dining
    when 1 then 'breakfast'
    when 2 then 'morning_snack'
    when 3 then 'lunch'
    when 4 then 'afternoon_snack'
    when 5 then 'dinner'
    when 6 then 'evening_snack'
    else 'other'
end,
updated_at = now();
