-- 月ごとの為替レート(円換算用)(適用済み)
create table if not exists kakeibo_exchange_rates (
  year_month text primary key check (year_month ~ '^\d{4}-\d{2}$'),
  usd_jpy numeric(10,2),
  khr_jpy numeric(10,4),
  updated_at timestamptz not null default now()
);

alter table kakeibo_exchange_rates enable row level security;
create policy "authenticated_all_exchange_rates" on kakeibo_exchange_rates
  for all to authenticated using (true) with check (true);
