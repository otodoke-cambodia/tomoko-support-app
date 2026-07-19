// Slack Events API の単一エンドポイント。
// #家計簿 チャンネル: レシート画像の自動記帳 + スレッド返信での訂正
// #tomokoの要望 チャンネル: 要望ヒアリングBot
import { createClient } from "npm:@supabase/supabase-js@2";
import { downloadFile, getUserDisplayName, postMessage, verifySlackSignature } from "../_shared/slack.ts";
import { continueHearing, extractReceipt, parseCorrection } from "../_shared/anthropic.ts";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

const KAKEIBO_CHANNEL_ID = Deno.env.get("SLACK_KAKEIBO_CHANNEL_ID");
const REQUEST_CHANNEL_ID = Deno.env.get("SLACK_REQUEST_CHANNEL_ID");

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

async function alreadyProcessed(eventId: string): Promise<boolean> {
  const { data } = await supabase.from("kakeibo_processed_events").select("event_id").eq("event_id", eventId).maybeSingle();
  if (data) return true;
  const { error } = await supabase.from("kakeibo_processed_events").insert({ event_id: eventId });
  // 一意制約違反 = 他のリクエストが先に処理済みにした = 重複扱い
  return !!error;
}

async function handleReceiptMessage(event: Record<string, any>) {
  const file = event.files?.find((f: any) => f.mimetype?.startsWith("image/"));
  if (!file) return;

  const userName = (await getUserDisplayName(event.user)) ?? event.user;
  const { bytes, contentType } = await downloadFile(file.url_private);
  const extraction = await extractReceipt(toBase64(bytes), contentType);

  const { error } = await supabase.from("kakeibo_transactions").insert({
    slack_user_id: event.user,
    slack_user_name: userName,
    store_name: extraction.store_name,
    purchased_at: extraction.purchased_at,
    amount: extraction.amount,
    category: extraction.category,
    items: extraction.items,
    memo: extraction.memo,
    receipt_image_url: file.url_private,
    slack_channel_id: event.channel,
    slack_message_ts: event.ts,
    raw_ai_response: extraction,
  });

  if (error) {
    console.error("insert kakeibo_transactions failed", error);
    await postMessage(event.channel, "⚠️ 記帳に失敗しました。もう一度写真を送ってもらえますか?", event.ts);
    return;
  }

  const itemsLine = extraction.items.length
    ? "\n" + extraction.items.map((i) => `・${i.name} ¥${i.price.toLocaleString()}`).join("\n")
    : "";
  await postMessage(
    event.channel,
    `🧾 ${extraction.store_name ?? "不明な店舗"} ¥${extraction.amount.toLocaleString()}(${extraction.category})で記帳しました${itemsLine}\n違っていたらこのスレッドで訂正内容を教えてください。`,
    event.ts,
  );
}

async function handleReceiptCorrection(event: Record<string, any>) {
  const { data: record } = await supabase
    .from("kakeibo_transactions")
    .select("*")
    .eq("slack_message_ts", event.thread_ts)
    .maybeSingle();
  if (!record) return; // 記帳と紐づかないスレッド返信(雑談など)は無視

  const correction = await parseCorrection(record, event.text);
  if (Object.keys(correction.updates).length === 0) {
    await postMessage(event.channel, correction.reply, event.thread_ts);
    return;
  }

  const { error } = await supabase
    .from("kakeibo_transactions")
    .update({ ...correction.updates, status: "corrected", updated_at: new Date().toISOString() })
    .eq("id", record.id);

  if (error) {
    console.error("update kakeibo_transactions failed", error);
    return;
  }
  await postMessage(event.channel, correction.reply, event.thread_ts);
}

async function handleHearingStart(event: Record<string, any>) {
  const userName = (await getUserDisplayName(event.user)) ?? event.user;
  const conversation = [{ role: "user", content: event.text }];
  const result = await continueHearing(conversation);

  await supabase.from("kakeibo_feature_requests").insert({
    requester_slack_id: event.user,
    requester_name: userName,
    slack_channel_id: event.channel,
    thread_ts: event.ts,
    title: result.title ?? null,
    status: result.done ? "proposed" : "hearing",
    conversation: [...conversation, { role: "assistant", content: result.reply }],
    proposal: result.proposal ?? null,
  });

  await postMessage(event.channel, result.reply, event.ts);
}

async function handleHearingContinue(event: Record<string, any>) {
  const { data: record } = await supabase
    .from("kakeibo_feature_requests")
    .select("*")
    .eq("thread_ts", event.thread_ts)
    .maybeSingle();
  if (!record || record.status !== "hearing") return;

  const conversation = [...(record.conversation as any[]), { role: "user", content: event.text }];
  const result = await continueHearing(conversation);
  const updatedConversation = [...conversation, { role: "assistant", content: result.reply }];

  await supabase
    .from("kakeibo_feature_requests")
    .update({
      conversation: updatedConversation,
      status: result.done ? "proposed" : "hearing",
      title: result.title ?? record.title,
      proposal: result.proposal ?? record.proposal,
      updated_at: new Date().toISOString(),
    })
    .eq("id", record.id);

  await postMessage(event.channel, result.reply, event.thread_ts);
}

async function processEvent(event: Record<string, any>) {
  // Bot自身の投稿(記帳確認メッセージなど)には反応しない
  if (event.bot_id || event.subtype === "bot_message") return;
  if (event.type !== "message") return;

  const isThreadReply = !!event.thread_ts && event.thread_ts !== event.ts;

  if (event.channel === KAKEIBO_CHANNEL_ID) {
    if (isThreadReply) {
      await handleReceiptCorrection(event);
    } else if (event.files?.length) {
      await handleReceiptMessage(event);
    }
    return;
  }

  if (event.channel === REQUEST_CHANNEL_ID) {
    if (isThreadReply) {
      await handleHearingContinue(event);
    } else if (event.text?.trim()) {
      await handleHearingStart(event);
    }
    return;
  }
}

Deno.serve(async (req) => {
  const rawBody = await req.text();

  const valid = await verifySlackSignature(req, rawBody);
  if (!valid) return new Response("invalid signature", { status: 401 });

  const payload = JSON.parse(rawBody);

  if (payload.type === "url_verification") {
    return new Response(JSON.stringify({ challenge: payload.challenge }), {
      headers: { "content-type": "application/json" },
    });
  }

  if (payload.type === "event_callback") {
    const isDuplicate = await alreadyProcessed(payload.event_id);
    if (!isDuplicate) {
      // @ts-ignore: Supabase Edge Runtime globalでEdgeRuntimeが利用可能
      EdgeRuntime.waitUntil(processEvent(payload.event).catch((e) => console.error("processEvent failed", e)));
    }
    return new Response("ok", { status: 200 });
  }

  return new Response("ignored", { status: 200 });
});
