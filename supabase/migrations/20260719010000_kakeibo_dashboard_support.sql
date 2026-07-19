-- ダッシュボード対応: カテゴリ細分化(食費→自炊/外食)、収入、手入力エントリ、認証ユーザーのアクセス(適用済み)

alter table kakeibo_transactions drop constraint if exists kakeibo_transactions_category_check;
update kakeibo_transactions set category = '食費(自炊)' where category = '食費';
alter table kakeibo_transactions add constraint kakeibo_transactions_category_check
  check (category in ('食費(自炊)','外食','日用品','交通','医療','娯楽','教育','住居','光熱費','通信','衣類','収入','その他'));

alter table kakeibo_transactions
  add column if not exists entry_type text not null default 'expense'
    check (entry_type in ('expense','income'));

alter table kakeibo_transactions alter column slack_user_id drop not null;
alter table kakeibo_transactions alter column slack_channel_id drop not null;
alter table kakeibo_transactions alter column slack_message_ts drop not null;

create policy "authenticated_all_transactions" on kakeibo_transactions
  for all to authenticated using (true) with check (true);
create policy "authenticated_read_requests" on kakeibo_feature_requests
  for select to authenticated using (true);
