-- レシートの購入時刻も記録する(適用済み)
alter table kakeibo_transactions
  add column if not exists purchased_time time;
