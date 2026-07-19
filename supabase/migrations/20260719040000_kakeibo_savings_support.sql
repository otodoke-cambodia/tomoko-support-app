-- 貯蓄・投資の入出金と口座別残高管理に対応(適用済み)
alter table kakeibo_transactions drop constraint if exists kakeibo_transactions_entry_type_check;
alter table kakeibo_transactions add constraint kakeibo_transactions_entry_type_check
  check (entry_type in ('expense','income','saving'));

alter table kakeibo_transactions add column if not exists account_name text;

-- amountは正(入金)/負(出金)を許容するため、支出・収入では引き続き正の値のみを想定(アプリ側で担保)
