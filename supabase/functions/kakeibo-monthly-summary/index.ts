// pg_cronから月初に呼ばれ、先月分のカテゴリ別支出サマリーをSlackに投稿する
import { createClient } from "npm:@supabase/supabase-js@2";
import { postMessage } from "../_shared/slack.ts";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

const KAKEIBO_CHANNEL_ID = Deno.env.get("SLACK_KAKEIBO_CHANNEL_ID")!;

function pad(n: number) {
  return String(n).padStart(2, "0");
}

// month: 0-11 (JS Dateと同じ)
function monthBounds(year: number, month: number) {
  const first = `${year}-${pad(month + 1)}-01`;
  const lastDate = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const last = `${year}-${pad(month + 1)}-${pad(lastDate)}`;
  return { first, last, label: `${year}年${month + 1}月` };
}

async function sumBetween(first: string, last: string) {
  const { data, error } = await supabase
    .from("kakeibo_transactions")
    .select("category, amount")
    .gte("purchased_at", first)
    .lte("purchased_at", last);
  if (error) throw error;
  return data as { category: string; amount: number }[];
}

Deno.serve(async (_req) => {
  const now = new Date();
  // 実行日の前月を対象にする(例: 8/1に実行 → 7月分を集計)
  const targetYear = now.getUTCMonth() === 0 ? now.getUTCFullYear() - 1 : now.getUTCFullYear();
  const targetMonth = (now.getUTCMonth() + 11) % 12;
  const prevYear = targetMonth === 0 ? targetYear - 1 : targetYear;
  const prevMonth = (targetMonth + 11) % 12;

  const target = monthBounds(targetYear, targetMonth);
  const prev = monthBounds(prevYear, prevMonth);

  const [targetRows, prevRows] = await Promise.all([
    sumBetween(target.first, target.last),
    sumBetween(prev.first, prev.last),
  ]);

  if (targetRows.length === 0) {
    await postMessage(KAKEIBO_CHANNEL_ID, `📊 ${target.label}の記帳データがありませんでした。`);
    return new Response("ok");
  }

  const byCategory = new Map<string, number>();
  let total = 0;
  for (const row of targetRows) {
    byCategory.set(row.category, (byCategory.get(row.category) ?? 0) + row.amount);
    total += row.amount;
  }
  const prevTotal = prevRows.reduce((sum, r) => sum + r.amount, 0);

  const diff = total - prevTotal;
  const diffLine = prevRows.length
    ? diff === 0
      ? "先月と同額でした"
      : `先月比 ${diff > 0 ? "+" : ""}¥${diff.toLocaleString()}`
    : "先月のデータなし";

  const categoryLines = [...byCategory.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([category, amount]) => `・${category}: ¥${amount.toLocaleString()}`)
    .join("\n");

  const message = `📊 ${target.label}の家計簿サマリー\n合計: ¥${total.toLocaleString()}(${diffLine})\n\n${categoryLines}`;

  await postMessage(KAKEIBO_CHANNEL_ID, message);
  return new Response("ok");
});
