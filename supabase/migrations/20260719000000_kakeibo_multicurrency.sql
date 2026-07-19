-- カンボジア在住のためUSD/KHR/JPYの多通貨対応にする(適用済み)
alter table kakeibo_transactions
  alter column amount type numeric(12,2);

alter table kakeibo_transactions
  add column if not exists currency text not null default 'USD'
    check (currency in ('USD','KHR','JPY'));
