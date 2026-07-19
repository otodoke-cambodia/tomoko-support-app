-- Tomoko Support APP: 家計簿自動化 + 要望ヒアリング用スキーマ
-- 既存のBlogプロジェクトのテーブルとは "kakeibo_" プレフィックスで区別する

create table if not exists kakeibo_transactions (
  id bigint generated always as identity primary key,
  slack_user_id text not null,
  slack_user_name text,
  store_name text,
  purchased_at date,
  amount integer not null,
  category text not null default 'その他'
    check (category in ('食費','日用品','交通','医療','娯楽','教育','住居','光熱費','通信','衣類','その他')),
  items jsonb not null default '[]'::jsonb,
  memo text,
  receipt_image_url text,
  slack_channel_id text not null,
  slack_message_ts text not null unique,
  thread_ts text,
  status text not null default 'confirmed' check (status in ('confirmed','corrected')),
  raw_ai_response jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists kakeibo_transactions_purchased_at_idx on kakeibo_transactions (purchased_at);
create index if not exists kakeibo_transactions_category_idx on kakeibo_transactions (category);

create table if not exists kakeibo_feature_requests (
  id bigint generated always as identity primary key,
  requester_slack_id text not null,
  requester_name text,
  slack_channel_id text not null,
  thread_ts text not null unique,
  title text,
  status text not null default 'hearing'
    check (status in ('hearing','proposed','backlog','approved','in_progress','done','rejected')),
  conversation jsonb not null default '[]'::jsonb,
  proposal jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Slackはタイムアウト時に同じイベントを再送してくるため、重複処理防止用に処理済みevent_idを記録する
create table if not exists kakeibo_processed_events (
  event_id text primary key,
  processed_at timestamptz not null default now()
);

alter table kakeibo_transactions enable row level security;
alter table kakeibo_feature_requests enable row level security;
alter table kakeibo_processed_events enable row level security;

-- Edge FunctionはService Role Keyで接続しRLSをバイパスするため、
-- ここでは匿名/authenticatedロール向けのポリシーは意図的に作成しない
