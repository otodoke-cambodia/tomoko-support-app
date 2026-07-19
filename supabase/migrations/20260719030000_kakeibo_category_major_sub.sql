-- カテゴリをZaimに合わせて大分類(category_major)+内訳(category_sub)の2列に変更(適用済み)
-- category_subは今後も種類が増えるため自由記述(CHECK制約なし)。category_majorはZaim標準の大分類に揃える

alter table kakeibo_transactions add column if not exists category_major text;
alter table kakeibo_transactions add column if not exists category_sub text;

update kakeibo_transactions set
  category_major = case category
    when '食費(自炊)' then '食費'
    when '外食' then '食費'
    when '日用品' then '日用雑貨'
    when '住居' then '住まい'
    when '光熱費' then '水道・光熱'
    when '交通' then '交通'
    when '医療' then '医療'
    when '娯楽' then '趣味・娯楽'
    when '教育' then '教育・教養'
    when '通信' then '通信費'
    when '衣類' then '衣服・美容'
    when '収入' then '収入'
    else 'その他'
  end,
  category_sub = case
    when category = '食費(自炊)' then '食料品'
    when category = '外食' then '外食'
    when category = '住居' then '家賃'
    when category = '収入' then '給与所得'
    else 'その他'
  end;

alter table kakeibo_transactions drop constraint if exists kakeibo_transactions_category_check;
alter table kakeibo_transactions drop column category;
alter table kakeibo_transactions alter column category_major set not null;
alter table kakeibo_transactions alter column category_sub set not null;
alter table kakeibo_transactions alter column category_sub set default 'その他';

create index if not exists kakeibo_transactions_category_major_idx on kakeibo_transactions (category_major);
