// Anthropic API 呼び出しの共通ヘルパー(Deno環境なのでSDKは使わずfetchで直接叩く)

const MODEL = Deno.env.get("ANTHROPIC_MODEL") || "claude-sonnet-5";

function apiKey() {
  const key = Deno.env.get("ANTHROPIC_API_KEY");
  if (!key) throw new Error("ANTHROPIC_API_KEY is not set");
  return key;
}

type ContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; source: { type: "base64"; media_type: string; data: string } };

async function callClaude(system: string, content: ContentBlock[], maxTokens = 1024): Promise<string> {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": apiKey(),
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: maxTokens,
      system,
      messages: [{ role: "user", content }],
    }),
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`Anthropic API error ${res.status}: ${errText}`);
  }
  const data = await res.json();
  return data.content?.[0]?.text ?? "";
}

function extractJson<T>(raw: string): T {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/);
  const jsonText = fenced ? fenced[1] : raw;
  return JSON.parse(jsonText.trim());
}

export interface ReceiptExtraction {
  store_name: string | null;
  purchased_at: string | null; // YYYY-MM-DD
  amount: number;
  category: "食費" | "日用品" | "交通" | "医療" | "娯楽" | "教育" | "住居" | "光熱費" | "通信" | "衣類" | "その他";
  items: { name: string; price: number }[];
  memo: string | null;
}

const RECEIPT_SYSTEM_PROMPT = `あなたは家計簿アプリのレシート読み取りアシスタントです。
レシート画像から情報を抽出し、必ず以下のJSON形式のみで出力してください。説明文やコードフェンスは不要です。

{
  "store_name": string | null,
  "purchased_at": "YYYY-MM-DD" | null,
  "amount": number,
  "category": "食費" | "日用品" | "交通" | "医療" | "娯楽" | "教育" | "住居" | "光熱費" | "通信" | "衣類" | "その他",
  "items": [{"name": string, "price": number}],
  "memo": string | null
}

categoryは購入内容全体から最も適切な1つを選んでください。日付が読み取れない場合はnullにしてください。
amountはレシート合計金額(円、数値のみ)を入れてください。`;

export async function extractReceipt(imageBase64: string, mediaType: string): Promise<ReceiptExtraction> {
  const raw = await callClaude(RECEIPT_SYSTEM_PROMPT, [
    { type: "image", source: { type: "base64", media_type: mediaType, data: imageBase64 } },
    { type: "text", text: "このレシート画像を読み取ってJSONで出力してください。" },
  ]);
  return extractJson<ReceiptExtraction>(raw);
}

export interface CorrectionResult {
  updates: Partial<Pick<ReceiptExtraction, "store_name" | "purchased_at" | "amount" | "category" | "memo">>;
  reply: string;
}

const CORRECTION_SYSTEM_PROMPT = `あなたは家計簿アプリの訂正アシスタントです。
直前に記帳した内容と、ユーザーからの訂正メッセージが与えられます。
訂正内容を反映したupdatesと、ユーザーへの短い確認返信(reply)を、必ず以下のJSON形式のみで出力してください。

{
  "updates": { "store_name"?: string, "purchased_at"?: "YYYY-MM-DD", "amount"?: number, "category"?: string, "memo"?: string },
  "reply": string
}

訂正が read み取れない項目は updates に含めないでください。categoryは食費/日用品/交通/医療/娯楽/教育/住居/光熱費/通信/衣類/その他 のいずれかにしてください。`;

export async function parseCorrection(
  currentRecord: Record<string, unknown>,
  correctionMessage: string,
): Promise<CorrectionResult> {
  const raw = await callClaude(CORRECTION_SYSTEM_PROMPT, [
    {
      type: "text",
      text: `現在の記帳内容:\n${JSON.stringify(currentRecord)}\n\nユーザーからの訂正メッセージ:\n${correctionMessage}`,
    },
  ]);
  return extractJson<CorrectionResult>(raw);
}

export interface HearingResult {
  done: boolean;
  reply: string;
  title?: string;
  proposal?: {
    problem: string;
    current_workaround: string;
    frequency: string;
    proposed_solution: string;
    priority: "high" | "medium" | "low";
  };
}

const HEARING_SYSTEM_PROMPT = `あなたは家事最適化AIアプリのプロダクトマネージャーです。
家族からSlackに投稿された「困りごと・要望」に対して、何を作るべきか具体化するために対話形式でヒアリングします。

方針:
- 一度に聞く質問は1つだけ。今何をしていて、どれくらいの頻度で困っているか、理想はどうなってほしいかを掘り下げる。
- 十分な情報(困りごとの内容・頻度・現状の対処法・理想の状態)が集まったら、done を true にして、簡潔な機能提案(proposal)をまとめる。
- まだ情報が不足していれば done は false にし、reply に次の質問を1つだけ書く。
- 会話は3〜4往復以内で収束させる。

必ず以下のJSON形式のみで出力してください。

{
  "done": boolean,
  "reply": string,
  "title": string,
  "proposal": {
    "problem": string,
    "current_workaround": string,
    "frequency": string,
    "proposed_solution": string,
    "priority": "high" | "medium" | "low"
  }
}

done が false の場合、proposal は省略してよいです。`;

export async function continueHearing(conversation: { role: string; content: string }[]): Promise<HearingResult> {
  const transcript = conversation.map((m) => `${m.role === "user" ? "家族" : "AI"}: ${m.content}`).join("\n");
  const raw = await callClaude(HEARING_SYSTEM_PROMPT, [
    { type: "text", text: `これまでの会話:\n${transcript}\n\n次のAIの応答をJSONで出力してください。` },
  ]);
  return extractJson<HearingResult>(raw);
}
