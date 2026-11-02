-- Sprint 15: an expense in a third currency (neither the trip's nor the home
-- currency) keeps its rate to the trip currency. Null for every other expense.
alter table expenses
    add column original_rate numeric,
    add column original_rate_date date,
    add column original_rate_provider text;
