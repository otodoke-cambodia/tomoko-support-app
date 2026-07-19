# Tomoko Support APP

家事最適化サポートAI。Slackに投げたレシート写真を夜間バッチで自動記帳し、家族の要望に機能提案を返す。

## 仕組み(夜間バッチ方式)

- **日中**: `#家計簿` にレシート写真を投稿するだけ(即時処理はしない)
- **毎晩23:30**: このMacのlaunchdがバッチを起動し、Claude CLI(**Proサブスクリプション枠**、API課金なし)で処理:
  1. 未記帳のレシート画像を読み取り(クメール語/英語/日本語対応、USD/KHR/JPY対応)→ Supabaseに記帳 → 各写真のスレッドに結果を返信
  2. 記帳済みスレッドへの訂正返信(「これは日用品だよ」等)を反映
  3. `#tomokoの要望` の新規投稿に機能提案を生成してスレッド返信
  4. 毎月1日は前月の通貨別・カテゴリ別サマリーを投稿
- 過去72時間分を毎晩見るので、失敗したレシートは翌晩自動リトライされる
- **注意**: 23:30にこのMacが起動している(スリープでない)必要がある。スリープ中だった場合、次に起きたタイミングで実行される

## データ(Supabase「Blog」プロジェクト共用: nlzewynymilzhrocygoa)

- `kakeibo_transactions` — 記帳データ(店名・日付・金額・通貨・カテゴリ・品目)
- `kakeibo_feature_requests` — 要望と提案(status: proposed → backlog → in_progress → done)
- ※ 旧構成のEdge Function(slack-events / kakeibo-monthly-summary)はデプロイ済みだが未使用(Secrets未設定のため動作しない)。pg_cronジョブは削除済み

## セットアップの残り(あなたの操作)

### 1. Slackチャンネル作成とBot招待
1. `#家計簿` と `#tomokoの要望` を作成
2. 各チャンネルで `/invite @Omochi`
3. 各チャンネルIDを控える(チャンネル名クリック→詳細の最下部の `C...`)

### 2. Supabase service_roleキーを取得
[Supabaseダッシュボード](https://supabase.com/dashboard/project/nlzewynymilzhrocygoa/settings/api) → API Keys → `service_role` をコピー

### 3. .env を埋める
[.env](.env) を開き、`SLACK_KAKEIBO_CHANNEL_ID` / `SLACK_REQUEST_CHANNEL_ID` / `SUPABASE_SERVICE_ROLE_KEY` を記入
(SLACK_BOT_TOKEN と SUPABASE_URL は記入済み)

### 4. 手動テスト
ターミナルで:
```bash
cd "/Users/shuyatokutake/AI APP/Tomoko Support APP"
python3 scripts/nightly_batch.py
```
`#家計簿` にレシート写真を1枚投げてから実行すると、スレッドに記帳結果が返ってくるはず。

### 5. 毎晩の自動実行を登録
```bash
cp "/Users/shuyatokutake/AI APP/Tomoko Support APP/launchd/com.tomoko.kakeibo-nightly.plist" ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tomoko.kakeibo-nightly.plist
```
解除したい場合:
```bash
launchctl bootout gui/$(id -u)/com.tomoko.kakeibo-nightly
```

## 運用メモ

- ログ: `logs/nightly.log`
- Slack Appに必要なBot Token Scopes(設定済み): `chat:write` `files:read` `channels:history` `groups:history` `users:read`
- チャンネルを非公開にした場合もBotを招待していれば動く(`groups:history` 済み)
- Claude Pro枠を使うため、レシートが極端に多い日(数十枚以上)は5時間あたりの利用上限に当たる可能性あり。その場合は翌晩に自動リトライされる
- Tomokoさんは無料版のままでOK(処理はこのMacのあなたのアカウントで実行される)

## ダッシュボード(dashboard/index.html)

ブラウザで開くだけで使える家計簿ダッシュボード。メール+パスワードのログイン付き(Supabase Auth、家族以外はデータを見られない)。

- 月送り / 全期間 / カテゴリ / テキスト検索(品目・店名・メモ)での絞り込み
- 通貨別(USD/KHR/JPY)の支出・収入・収支と、カテゴリ別内訳バー
- 品目検索: 「醤油」で検索すると、どの店でいくらだったかがハイライト表示される
- 手入力での収入・支出の追加、既存明細の編集・削除
- 食費は「食費(自炊)」と「外食」に自動分類される(夜間バッチが判定)

使い方: `dashboard/index.html` をブラウザで開く(ダブルクリックでOK)。TomokoさんのPCにはこのファイルをコピーして渡せばよい(データは全てSupabase側にあり、ログインしないと見えない)。

ローカルでの動作確認は `.claude/launch.json` の `dashboard` サーバー(http://localhost:8765)でも可。

ユーザー追加(私=Claudeに頼めば追加します): Supabase Auth管理APIで作成。初期パスワードは `logs/.temp_pw` に置き、ログイン後に「パスワード変更」ボタンで変更を推奨。

## ファイル構成

```
.env                    # 認証情報(コミット禁止)
scripts/
  nightly_batch.py      # バッチ本体(Python標準ライブラリのみ)
  run_nightly.sh        # launchd用ラッパー(ログ出力・ローテーション)
launchd/
  com.tomoko.kakeibo-nightly.plist  # 毎晩23:30実行の定義
supabase/
  migrations/           # DBスキーマ(適用済み)
  functions/            # 旧構成のEdge Function(未使用・参考用)
logs/                   # 実行ログ
```
