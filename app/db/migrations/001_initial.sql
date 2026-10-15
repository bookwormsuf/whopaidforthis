-- Sprint 9: Postgres schema replacing Google Sheets as the store.
-- Spec references: docs/sprints/09-neon-store-and-export.md, pinned-decisions.md §18.
-- Money is always bigint minor units; Telegram IDs are bigint; expense IDs are the
-- draft's ULID (text).

create table if not exists trips (
    id bigserial primary key,
    chat_id bigint not null,
    name text,
    trip_currency char(3),
    home_currency char(3),
    timezone text,
    created_by bigint,
    created_at timestamptz not null default now(),
    closed_at timestamptz null
);

-- One open trip per chat.
create unique index if not exists trips_one_open_per_chat
    on trips (chat_id)
    where closed_at is null;

create table if not exists trip_users (
    trip_id bigint not null references trips (id) on delete cascade,
    user_id bigint not null,
    username text,
    display_name text,
    joined boolean not null default false,
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null,
    primary key (trip_id, user_id)
);

create table if not exists expenses (
    id text primary key,
    trip_id bigint not null references trips (id) on delete cascade,
    chat_id bigint not null,
    message_id bigint not null,
    created_by bigint not null,
    payer_id bigint not null,
    description text not null,
    merchant text null,
    category text not null,
    original_amount_minor bigint not null,
    original_currency char(3) not null,
    trip_amount_minor bigint not null,
    home_amount_minor bigint not null,
    rate numeric(18, 8) not null,
    rate_date date not null,
    transaction_date date not null,
    source_type text not null,
    split_method text not null,
    status text not null,
    created_at timestamptz not null,
    cancelled_at timestamptz null,
    notes text null,
    constraint expenses_source_type_check check (source_type in ('text', 'receipt')),
    constraint expenses_status_check check (status in ('confirmed', 'cancelled')),
    constraint expenses_split_method_check check (split_method in ('equal', 'custom', 'items'))
);

create unique index if not exists expenses_one_per_message
    on expenses (chat_id, message_id);

create index if not exists expenses_trip_status_date
    on expenses (trip_id, status, transaction_date);

create table if not exists splits (
    expense_id text not null references expenses (id) on delete cascade,
    user_id bigint not null,
    share_trip_minor bigint not null,
    share_home_minor bigint not null,
    split_method text not null,
    constraint splits_split_method_check check (split_method in ('equal', 'custom', 'items')),
    primary key (expense_id, user_id)
);

create table if not exists line_items (
    expense_id text not null references expenses (id) on delete cascade,
    position int not null,
    description text not null,
    quantity int not null,
    amount_trip_minor bigint not null,
    kind text not null,
    allocation_method text not null,
    assigned_user_ids bigint[] not null default '{}',
    constraint line_items_kind_check
        check (kind in ('item', 'tax', 'service_charge', 'discount')),
    constraint line_items_allocation_method_check
        check (allocation_method in ('claimed', 'everyone', 'proportional', 'unassigned')),
    primary key (expense_id, position)
);

-- Global, not per trip: a rate is a fact about a currency pair and a date.
create table if not exists exchange_rates (
    base char(3) not null,
    quote char(3) not null,
    requested_date date not null,
    rate_date date not null,
    rate numeric(18, 8) not null,
    provider text not null,
    retrieved_at timestamptz not null,
    primary key (base, quote, requested_date)
);
