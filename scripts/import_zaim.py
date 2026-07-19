#!/usr/bin/env python3
"""Zaimエクスポート(UTF-8 CSV)をkakeibo_transactionsへ一括投入する一回限りのスクリプト。

category_major/category_subはZaimの「カテゴリ」「カテゴリの内訳」をそのまま使う
(大分類名だけ一部をこのアプリの表記に統一)。
"""
import csv
import json
import sys
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"


def load_env() -> dict:
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


ENV = load_env()

# Zaimの大分類名 → このアプリの大分類名(表記ゆれの統一のみ。基本はZaimそのまま)
MAJOR_MAP = {
    "給与所得": "収入",
}


def map_major(cat: str) -> str:
    return MAJOR_MAP.get(cat, cat)


def supabase_insert(rows: list[dict]) -> None:
    req = urllib.request.Request(
        f"{ENV['SUPABASE_URL']}/rest/v1/kakeibo_transactions",
        data=json.dumps(rows, ensure_ascii=False).encode(),
        headers={
            "apikey": ENV["SUPABASE_SERVICE_ROLE_KEY"],
            "Authorization": f"Bearer {ENV['SUPABASE_SERVICE_ROLE_KEY']}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        if res.status not in (200, 201):
            raise RuntimeError(f"insert failed: {res.status} {res.read()}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: import_zaim.py <csv_path>")
    csv_path = Path(sys.argv[1])

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    payload = []
    skipped = 0
    for r in rows:
        income = float(r["収入"] or 0)
        expense = float(r["支出"] or 0)
        if r["方法"] == "income":
            entry_type, amount = "income", income
            store = r["入金先"] if r["入金先"] != "-" else None
        elif r["方法"] == "payment":
            entry_type, amount = "expense", expense
            store = r["お店"] or None
        else:
            skipped += 1
            continue
        if amount == 0:
            skipped += 1
            continue

        major = map_major(r["カテゴリ"])
        sub = r["カテゴリの内訳"] if r["カテゴリの内訳"] and r["カテゴリの内訳"] != "-" else r["カテゴリ"]

        memo_parts = [p for p in (r["品目"], r["メモ"]) if p and p.strip()]
        payload.append({
            "entry_type": entry_type,
            "purchased_at": r["日付"],
            "store_name": store,
            "amount": amount,
            "currency": "USD",
            "category_major": major,
            "category_sub": sub,
            "items": [],
            "memo": (" / ".join(memo_parts) + " [Zaimインポート]") if memo_parts else "[Zaimインポート]",
        })

    print(f"取り込み対象: {len(payload)}件 (スキップ: {skipped}件)")
    BATCH = 50
    for i in range(0, len(payload), BATCH):
        supabase_insert(payload[i : i + BATCH])
        print(f"  {min(i + BATCH, len(payload))}/{len(payload)} 件投入済み")
    print("完了")


if __name__ == "__main__":
    main()
