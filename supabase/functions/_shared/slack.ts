// Slack Events API まわりの共通ヘルパー

export async function verifySlackSignature(req: Request, rawBody: string): Promise<boolean> {
  const signingSecret = Deno.env.get("SLACK_SIGNING_SECRET");
  if (!signingSecret) throw new Error("SLACK_SIGNING_SECRET is not set");

  const timestamp = req.headers.get("x-slack-request-timestamp");
  const signature = req.headers.get("x-slack-signature");
  if (!timestamp || !signature) return false;

  // 5分以上ズレたリクエストはリプレイ攻撃の可能性があるため拒否
  const age = Math.abs(Date.now() / 1000 - Number(timestamp));
  if (age > 60 * 5) return false;

  const base = `v0:${timestamp}:${rawBody}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(base));
  const digest = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const expected = `v0=${digest}`;

  if (expected.length !== signature.length) return false;
  let mismatch = 0;
  for (let i = 0; i < expected.length; i++) {
    mismatch |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return mismatch === 0;
}

const botToken = () => {
  const token = Deno.env.get("SLACK_BOT_TOKEN");
  if (!token) throw new Error("SLACK_BOT_TOKEN is not set");
  return token;
};

export async function postMessage(channel: string, text: string, threadTs?: string) {
  const res = await fetch("https://slack.com/api/chat.postMessage", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${botToken()}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: JSON.stringify({ channel, text, thread_ts: threadTs }),
  });
  const data = await res.json();
  if (!data.ok) console.error("slack.chat.postMessage failed", data);
  return data;
}

export async function downloadFile(url: string): Promise<{ bytes: Uint8Array; contentType: string }> {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${botToken()}` } });
  if (!res.ok) throw new Error(`Slack file download failed: ${res.status}`);
  const contentType = res.headers.get("content-type") ?? "image/jpeg";
  const bytes = new Uint8Array(await res.arrayBuffer());
  return { bytes, contentType };
}

export async function getThreadReplies(channel: string, threadTs: string) {
  const params = new URLSearchParams({ channel, ts: threadTs });
  const res = await fetch(`https://slack.com/api/conversations.replies?${params}`, {
    headers: { Authorization: `Bearer ${botToken()}` },
  });
  const data = await res.json();
  if (!data.ok) {
    console.error("slack.conversations.replies failed", data);
    return [];
  }
  return data.messages as Array<{ user?: string; bot_id?: string; text: string; ts: string }>;
}

export async function getUserDisplayName(userId: string): Promise<string | undefined> {
  const res = await fetch(`https://slack.com/api/users.info?user=${userId}`, {
    headers: { Authorization: `Bearer ${botToken()}` },
  });
  const data = await res.json();
  if (!data.ok) return undefined;
  return data.user?.profile?.display_name || data.user?.real_name;
}
