#!/usr/bin/env python3
"""Tomoko Support APP 夜間バッチ。

毎晩launchdから起動され、以下を1回で処理する:
1. #家計簿 の未記帳レシート画像、および写真のないテキストのみの支出/収入投稿を
   Claude CLI (Pro枠) で読み取り・解析 → Supabase記帳 → スレッド返信
2. #tomokoの要望 の新規投稿に機能提案を生成 → Supabase保存 → スレッド返信
3. 毎月1日は前月の通貨別・カテゴリ別サマリーを投稿

外部依存なし(標準ライブラリのみ)。認証情報は同階層の ../.env から読む。
"""

import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
CLAUDE_BIN = str(Path.home() / ".local/bin/claude")

LOOKBACK_HOURS = 72  # この時間内の未処理メッセージを対象にする(失敗時は翌晩リトライされる)
CURRENCY_SYMBOL = {"USD": "$", "KHR": "៛", "JPY": "¥"}
KHR_TO_USD = 4000  # 1USD = 4000KHR固定レート。リエルはSlack返信・サマリーで常にドル換算して見せる


def display_amount(amount: float, currency: str) -> str:
    """リエルは1USD=4000KHR固定でドル換算し、元のリエル額も併記する。"""
    if currency == "KHR":
        return f"${amount / KHR_TO_USD:,.2f}(៛{amount:,.0f})"
    return f"{CURRENCY_SYMBOL.get(currency, '')}{amount:,.2f}"

# Zaimの分類体系(大分類→内訳)に合わせたカテゴリ構造。内訳はDB上は自由記述だが、
# AIにはこの一覧から選ばせることで既存データと表記を揃える。
CATEGORY_STRUCTURE = {
    "食費": ["食料品", "朝ご飯", "昼ご飯", "晩ご飯", "外食", "カフェ", "会食", "お酒", "その他"],
    "日用雑貨": ["消耗品", "その他"],
    "住まい": ["家賃", "住宅ローン", "修繕・リフォーム", "その他"],
    "水道・光熱": ["電気料金", "ガス料金", "水道料金", "その他"],
    "クルマ": ["ガソリン", "駐車場", "車検・整備", "保険", "その他"],
    "交通": ["電車", "バス", "タクシー", "飛行機", "その他"],
    "医療": ["病院", "薬", "その他"],
    "趣味・娯楽": ["旅行", "書籍", "映画・音楽", "ゲーム", "その他"],
    "教育・教養": ["学費", "参考書", "セミナー", "その他"],
    "通信費": ["携帯電話", "インターネット", "その他"],
    "衣服・美容": ["衣服", "美容", "その他"],
    "交際費": ["プレゼント", "交際費", "その他"],
    "収入": ["給与所得", "お小遣い", "賞与", "副業", "その他"],
    "その他": ["その他"],
}
CATEGORY_HINT = "\n".join(f"- {major}: {'/'.join(subs)}" for major, subs in CATEGORY_STRUCTURE.items())


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_env() -> dict:
    env = {}
    if not ENV_PATH.exists():
        sys.exit(f".env not found: {ENV_PATH}")
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    required = [
        "SLACK_BOT_TOKEN",
        "SLACK_KAKEIBO_CHANNEL_ID",
        "SLACK_REQUEST_CHANNEL_ID",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
    ]
    missing = [k for k in required if not env.get(k)]
    if missing:
        sys.exit(f".env の設定が未完了のため終了します。未設定: {', '.join(missing)}")
    return env


ENV = load_env()


# ---------- HTTP helpers ----------

def http_json(url: str, headers: dict, data: bytes | None = None, method: str = "GET") -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as res:
        body = res.read()
    return json.loads(body) if body else {}


def slack_get(api: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    return http_json(
        f"https://slack.com/api/{api}?{qs}",
        {"Authorization": f"Bearer {ENV['SLACK_BOT_TOKEN']}"},
    )


def slack_post_message(channel: str, text: str, thread_ts: str | None = None) -> None:
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    result = http_json(
        "https://slack.com/api/chat.postMessage",
        {
            "Authorization": f"Bearer {ENV['SLACK_BOT_TOKEN']}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json.dumps(payload, ensure_ascii=False).encode(),
        method="POST",
    )
    if not result.get("ok"):
        log(f"chat.postMessage failed: {result.get('error')}")


def slack_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {ENV['SLACK_BOT_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=120) as res:
        dest.write_bytes(res.read())


def supabase(path: str, method: str = "GET", payload: dict | list | None = None, prefer: str | None = None):
    headers = {
        "apikey": ENV["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {ENV['SUPABASE_SERVICE_ROLE_KEY']}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    return http_json(f"{ENV['SUPABASE_URL']}/rest/v1/{path}", headers, data, method)


# ---------- Claude CLI ----------

def run_claude(prompt: str, system_prompt: str, add_dir: str | None = None, tools: str = "") -> str:
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--output-format", "json",
        "--safe-mode",
        "--model", "sonnet",
        "--system-prompt", system_prompt,
        "--tools", tools,
        "--no-session-persistence",
    ]
    if add_dir:
        cmd += ["--add-dir", add_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {proc.stderr[:500]}")
    result = json.loads(proc.stdout)
    if result.get("is_error"):
        raise RuntimeError(f"claude returned error: {result.get('result', '')[:500]}")
    return result.get("result", "")


def extract_json(raw: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    text = fenced.group(1) if fenced else raw
    # 先頭の説明文が混ざった場合に備えて最初の { から最後の } までを取る
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON found in: {raw[:200]}")
    return json.loads(text[start : end + 1])


RECEIPT_SYSTEM = "あなたはレシート読み取りアシスタントです。指示されたJSON形式のみを出力し、説明文は書きません。"

RECEIPT_PROMPT = """{path} はレシート画像です。Readツールで読み取り、以下のJSONのみを出力してください。

{{
  "store_name": string | null,
  "purchased_at": "YYYY-MM-DD" | null,
  "purchased_time": "HH:MM" | null,
  "amount": number,
  "currency": "USD" | "KHR" | "JPY",
  "category_major": 下の大分類のいずれか1つ,
  "category_sub": 選んだ大分類に対応する内訳のいずれか1つ,
  "items": [{{"name": string, "price": number}}],
  "memo": string | null
}}

カテゴリ一覧(大分類: 内訳):
{category_hint}

重要な注意:
- 【リエル記号】カンボジアのレシートでは金額の先頭に riel 記号「៛」や「R」が付くことが多い。これは通貨記号であり、数字の「1」ではない。「៛9,400」を「19400」と読まないこと。
- 【検算(必須)】出力前に必ず次の手順を実行する: (1) items の price をすべて足し算する (2) その合計が amount(レシート合計)と一致するか確認する (3) 一致しない場合、通貨記号を数字の先頭桁として誤読していないか(桁数が1つ多くないか)を疑って読み直す (4) それでも一致しない場合のみ、割引・税・端数などの理由を memo に書く。
- 【日付】カンボジアのレシートは DD/MM/YYYY または DD-MM-YY 形式が多い。月と日を取り違えないこと。時刻(HH:MM)もレシートに印字されていれば読み取る。
- 【品名の翻訳(必須)】品名は必ず日本語に訳して出力する。クメール語や英語の原文のまま残さないこと。単語の意味を完全には特定できない場合でも、食料品店のレシートという文脈から「豚肉(部位不明)」「野菜(種類不明)」のように具体性のある日本語ラベルを付け、末尾に「(推定)」を付けて不確実性を示す。商品コードや数字だけの羅列は品名にしない。
- amount はレシートの合計金額。KHRの場合は整数。
- レストラン・カフェ・屋台での飲食は「食費」の「外食」または時間帯に応じて「朝ご飯」「昼ご飯」「晩ご飯」、スーパー等での食材購入は「食費」の「食料品」。
- 読み取れない項目は null。
- 画像がレシートでない場合は {{"not_receipt": true}} とだけ出力。"""

TEXT_ENTRY_SYSTEM = "あなたは家計簿アシスタントです。指示されたJSON形式のみを出力し、説明文は書きません。"

TEXT_ENTRY_PROMPT = """次のテキストは家計簿チャンネルへの投稿です。レシートの写真がない支出・収入を、文章で記録しようとしている可能性があります。

---
{text}
---

以下のJSONのみを出力してください。

{{
  "entry_type": "expense" | "income",
  "store_name": string | null,
  "purchased_at": "YYYY-MM-DD" | null(投稿文に日付が書かれていなければnull。投稿日が自動的に使われる),
  "amount": number,
  "currency": "USD" | "KHR" | "JPY"(明記がなければ文脈上最も自然な通貨。カンボジアの生活なので通常はUSD),
  "category_major": 下の大分類のいずれか1つ,
  "category_sub": 選んだ大分類に対応する内訳のいずれか1つ,
  "memo": string | null
}}

カテゴリ一覧(大分類: 内訳):
{category_hint}

金額や取引の内容が読み取れない雑談・質問・要望などの投稿の場合は {{"not_entry": true}} とだけ出力してください。"""

HEARING_SYSTEM = "あなたは家事最適化AIのプロダクトマネージャーです。指示されたJSON形式のみを出力し、説明文は書きません。"

HEARING_PROMPT = """家族がSlackの要望チャンネルに次の投稿をしました:

---
{text}
---

この困りごとを解決する機能の提案を、以下のJSONのみで出力してください。

{{
  "title": string(20字以内の要約),
  "reply": string(投稿者へのSlack返信文。共感を一言→提案内容を2〜3行で説明→「もっと詳しく聞きたいことがあれば返信してね」で締める。日本語、絵文字を1〜2個),
  "proposal": {{
    "problem": string,
    "proposed_solution": string,
    "priority": "high" | "medium" | "low"
  }}
}}

雑談や要望でない投稿の場合は {{"not_request": true}} とだけ出力。"""


# ---------- 1. レシート処理 ----------

def already_recorded(ts: str) -> bool:
    rows = supabase(f"kakeibo_transactions?select=id&slack_message_ts=eq.{ts}")
    return bool(rows)


def process_receipts() -> int:
    channel = ENV["SLACK_KAKEIBO_CHANNEL_ID"]
    oldest = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp()
    history = slack_get("conversations.history", {"channel": channel, "oldest": oldest, "limit": 200})
    if not history.get("ok"):
        log(f"conversations.history failed: {history.get('error')}")
        return 0

    count = 0
    for msg in reversed(history.get("messages", [])):  # 古い順に処理
        if msg.get("bot_id") or msg.get("subtype"):
            continue
        images = [f for f in msg.get("files", []) if f.get("mimetype", "").startswith("image/")]
        if not images:
            count += process_text_entry(channel, msg)
            continue
        if already_recorded(msg["ts"]):
            continue

        for idx, f in enumerate(images):
            # 複数画像の場合は2枚目以降のtsにサフィックスを付けて一意にする
            record_ts = msg["ts"] if idx == 0 else f"{msg['ts']}#{idx}"
            if idx > 0 and already_recorded(record_ts):
                continue
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    ext = (f.get("name") or "receipt.jpg").rsplit(".", 1)[-1]
                    img_path = Path(tmp) / f"receipt.{ext}"
                    slack_download(f["url_private"], img_path)
                    raw = run_claude(
                        RECEIPT_PROMPT.format(path=img_path, category_hint=CATEGORY_HINT),
                        RECEIPT_SYSTEM,
                        add_dir=tmp,
                        tools="Read",
                    )
                data = extract_json(raw)
                if data.get("not_receipt"):
                    slack_post_message(channel, "🤔 この画像はレシートとして読み取れませんでした。", msg["ts"])
                    continue

                supabase(
                    "kakeibo_transactions?on_conflict=slack_message_ts",
                    "POST",
                    {
                        "slack_user_id": msg.get("user", ""),
                        "store_name": data.get("store_name"),
                        "purchased_at": data.get("purchased_at"),
                        "purchased_time": data.get("purchased_time"),
                        "amount": data.get("amount", 0),
                        "currency": data.get("currency", "USD"),
                        "category_major": data.get("category_major", "その他"),
                        "category_sub": data.get("category_sub", "その他"),
                        "items": data.get("items", []),
                        "memo": data.get("memo"),
                        "receipt_image_url": f.get("url_private"),
                        "slack_channel_id": channel,
                        "slack_message_ts": record_ts,
                        "raw_ai_response": data,
                    },
                    prefer="resolution=ignore-duplicates,return=minimal",
                )
                amount_display = display_amount(data.get("amount", 0), data.get("currency", "USD"))
                items = data.get("items") or []
                items_line = "".join(f"\n・{i.get('name')} {i.get('price')}" for i in items[:10])
                when = " ".join(filter(None, [data.get("purchased_at"), data.get("purchased_time")]))
                when_part = f"{when} " if when else ""
                cat_label = f"{data.get('category_major')}/{data.get('category_sub')}"
                slack_post_message(
                    channel,
                    f"🧾 {when_part}{data.get('store_name') or '店名不明'} {amount_display}({cat_label})で記帳しました{items_line}\n違っていたらこのスレッドで教えてください(翌晩までに直します)。",
                    msg["ts"],
                )
                count += 1
                log(f"recorded receipt ts={record_ts} {data.get('store_name')} {amount_display}")
            except Exception as e:
                log(f"receipt failed ts={msg['ts']}: {e}")
    return count


def process_text_entry(channel: str, msg: dict) -> int:
    """写真なしのテキストのみの投稿を支出/収入として記帳する(雑談等は not_entry で無視)。"""
    text = (msg.get("text") or "").strip()
    if not text or already_recorded(msg["ts"]):
        return 0
    try:
        raw = run_claude(TEXT_ENTRY_PROMPT.format(text=text, category_hint=CATEGORY_HINT), TEXT_ENTRY_SYSTEM)
        data = extract_json(raw)
        if data.get("not_entry"):
            return 0

        purchased_at = data.get("purchased_at") or datetime.fromtimestamp(float(msg["ts"])).strftime("%Y-%m-%d")
        entry_type = data.get("entry_type") if data.get("entry_type") in ("expense", "income") else "expense"
        supabase(
            "kakeibo_transactions?on_conflict=slack_message_ts",
            "POST",
            {
                "entry_type": entry_type,
                "slack_user_id": msg.get("user", ""),
                "store_name": data.get("store_name"),
                "purchased_at": purchased_at,
                "amount": data.get("amount", 0),
                "currency": data.get("currency", "USD"),
                "category_major": data.get("category_major", "その他"),
                "category_sub": data.get("category_sub", "その他"),
                "items": [],
                "memo": data.get("memo"),
                "slack_channel_id": channel,
                "slack_message_ts": msg["ts"],
                "raw_ai_response": data,
            },
            prefer="resolution=ignore-duplicates,return=minimal",
        )
        amount_display = display_amount(data.get("amount", 0), data.get("currency", "USD"))
        cat_label = f"{data.get('category_major')}/{data.get('category_sub')}"
        sign = "+" if entry_type == "income" else ""
        slack_post_message(
            channel,
            f"✍️ {purchased_at} {data.get('store_name') or '(店名なし)'} {sign}{amount_display}({cat_label})で記帳しました\n違っていたらこのスレッドで教えてください(翌晩までに直します)。",
            msg["ts"],
        )
        log(f"recorded text entry ts={msg['ts']} {data.get('store_name')} {amount_display}")
        return 1
    except Exception as e:
        log(f"text entry failed ts={msg['ts']}: {e}")
        return 0


# ---------- 2. 訂正処理(記帳済みスレッドへの返信) ----------

def process_corrections() -> int:
    channel = ENV["SLACK_KAKEIBO_CHANNEL_ID"]
    oldest = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp()
    history = slack_get("conversations.history", {"channel": channel, "oldest": oldest, "limit": 200})
    if not history.get("ok"):
        return 0

    count = 0
    for msg in history.get("messages", []):
        if msg.get("reply_count", 0) == 0:
            continue
        rows = supabase(f"kakeibo_transactions?select=*&slack_message_ts=eq.{msg['ts']}")
        if not rows:
            continue
        record = rows[0]
        replies = slack_get("conversations.replies", {"channel": channel, "ts": msg["ts"]})
        if not replies.get("ok"):
            continue
        # 記帳後に人間が書いた最後の返信を訂正指示とみなす(botの返信より新しいもののみ)
        last_bot_ts = max((r["ts"] for r in replies["messages"] if r.get("bot_id")), default="0")
        human_after_bot = [
            r for r in replies["messages"]
            if not r.get("bot_id") and r["ts"] > last_bot_ts and r["ts"] != msg["ts"] and r.get("text")
        ]
        if not human_after_bot:
            continue
        correction_text = "\n".join(r["text"] for r in human_after_bot)
        try:
            raw = run_claude(
                f"""家計簿の記帳内容:
{json.dumps({k: record[k] for k in ('store_name', 'purchased_at', 'purchased_time', 'amount', 'currency', 'category_major', 'category_sub', 'memo')}, ensure_ascii=False)}

ユーザーからの訂正メッセージ:
{correction_text}

カテゴリ一覧(大分類: 内訳):
{CATEGORY_HINT}

訂正を反映するJSONのみを出力:
{{"updates": {{"store_name"?, "purchased_at"?, "purchased_time"?, "amount"?, "currency"?, "category_major"?, "category_sub"?, "memo"?}}, "reply": "短い確認返信(日本語)"}}
訂正と読み取れない場合は {{"updates": {{}}, "reply": "返答文"}}""",
                RECEIPT_SYSTEM,
            )
            data = extract_json(raw)
            updates = data.get("updates") or {}
            if updates:
                updates["status"] = "corrected"
                supabase(f"kakeibo_transactions?id=eq.{record['id']}", "PATCH", updates, prefer="return=minimal")
                slack_post_message(channel, f"✏️ {data.get('reply', '訂正しました。')}", msg["ts"])
                count += 1
                log(f"corrected ts={msg['ts']}: {updates}")
        except Exception as e:
            log(f"correction failed ts={msg['ts']}: {e}")
    return count


# ---------- 3. 要望への提案 ----------

def process_requests() -> int:
    channel = ENV["SLACK_REQUEST_CHANNEL_ID"]
    oldest = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp()
    history = slack_get("conversations.history", {"channel": channel, "oldest": oldest, "limit": 200})
    if not history.get("ok"):
        log(f"requests history failed: {history.get('error')}")
        return 0

    count = 0
    for msg in reversed(history.get("messages", [])):
        if msg.get("bot_id") or msg.get("subtype") or msg.get("thread_ts"):
            continue
        if not msg.get("text", "").strip():
            continue
        if supabase(f"kakeibo_feature_requests?select=id&thread_ts=eq.{msg['ts']}"):
            continue
        try:
            raw = run_claude(HEARING_PROMPT.format(text=msg["text"]), HEARING_SYSTEM)
            data = extract_json(raw)
            if data.get("not_request"):
                continue
            supabase(
                "kakeibo_feature_requests?on_conflict=thread_ts",
                "POST",
                {
                    "requester_slack_id": msg.get("user", ""),
                    "slack_channel_id": channel,
                    "thread_ts": msg["ts"],
                    "title": data.get("title"),
                    "status": "proposed",
                    "conversation": [
                        {"role": "user", "content": msg["text"]},
                        {"role": "assistant", "content": data.get("reply", "")},
                    ],
                    "proposal": data.get("proposal"),
                },
                prefer="resolution=ignore-duplicates,return=minimal",
            )
            slack_post_message(channel, data.get("reply", ""), msg["ts"])
            count += 1
            log(f"proposed for ts={msg['ts']}: {data.get('title')}")
        except Exception as e:
            log(f"request failed ts={msg['ts']}: {e}")
    return count


# ---------- 4. 月次サマリー ----------

def monthly_summary() -> None:
    today = datetime.now()
    if today.day != 1:
        return
    last_month_end = today.replace(day=1) - timedelta(days=1)
    first = last_month_end.replace(day=1).strftime("%Y-%m-%d")
    last = last_month_end.strftime("%Y-%m-%d")
    label = f"{last_month_end.year}年{last_month_end.month}月"

    rows = supabase(
        f"kakeibo_transactions?select=category_major,amount,currency&purchased_at=gte.{first}&purchased_at=lte.{last}"
    )
    if not rows:
        slack_post_message(ENV["SLACK_KAKEIBO_CHANNEL_ID"], f"📊 {label}の記帳データがありませんでした。")
        return

    def amount_in_usd(r: dict) -> float:
        v = float(r["amount"])
        return v / KHR_TO_USD if r["currency"] == "KHR" else v

    lines = [f"📊 {label}の家計簿サマリー"]
    for currency in ("USD", "JPY"):
        cur_rows = [r for r in rows if r["currency"] == currency or (currency == "USD" and r["currency"] == "KHR")]
        if not cur_rows:
            continue
        sym = CURRENCY_SYMBOL[currency]
        label_suffix = "(リエル換算込み)" if currency == "USD" and any(r["currency"] == "KHR" for r in rows) else ""
        total = sum(amount_in_usd(r) for r in cur_rows)
        lines.append(f"\n【{currency}{label_suffix}】合計 {sym}{total:,.2f}")
        by_cat: dict[str, float] = {}
        for r in cur_rows:
            by_cat[r["category_major"]] = by_cat.get(r["category_major"], 0) + amount_in_usd(r)
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"・{cat}: {sym}{amt:,.2f}")

    slack_post_message(ENV["SLACK_KAKEIBO_CHANNEL_ID"], "\n".join(lines))
    log("monthly summary posted")


def main() -> None:
    log("=== nightly batch start ===")
    receipts = process_receipts()
    corrections = process_corrections()
    requests_ = process_requests()
    monthly_summary()
    log(f"=== done: receipts={receipts} corrections={corrections} requests={requests_} ===")


if __name__ == "__main__":
    main()
