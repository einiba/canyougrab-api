import { PROVIDERS, type ProviderAdapter } from "../types";
import { buildPrompt, parseBases } from "../prompt";

const API_URL = "https://api.openai.com/v1/chat/completions";
const MODELS_URL = "https://api.openai.com/v1/models";

export const openaiAdapter: ProviderAdapter = {
  meta: PROVIDERS.openai,

  async listModels(key: string) {
    try {
      const res = await fetch(MODELS_URL, {
        headers: { Authorization: `Bearer ${key}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        return { ok: false, error: data?.error?.message ?? `HTTP ${res.status}` };
      }
      const data = await res.json();
      const ids: string[] = (data?.data ?? [])
        .map((m: { id?: string }) => m?.id)
        .filter((id: unknown): id is string => typeof id === "string");

      const KEEP = /^(gpt-|chatgpt-|o1|o3|o4)/i;
      const DROP = /-(instruct|embedding|embeddings|audio|realtime|tts|transcribe|search|moderation|image|video)\b|^(dall-e|whisper|tts-)/i;
      const chat = ids.filter((id) => KEEP.test(id) && !DROP.test(id));
      chat.sort();
      return { ok: true, models: chat };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "Network error" };
    }
  },

  async generateBases({ key, model, description, styles, tldPref, count, anchors, exclude }) {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        model,
        max_tokens: 1024,
        messages: [{ role: "user", content: buildPrompt({ description, styles, tldPref, count, anchors, exclude }) }],
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data?.error?.message ?? `OpenAI ${res.status}`);
    }
    const data = await res.json();
    const text = data?.choices?.[0]?.message?.content ?? "";
    return parseBases(text, count);
  },
};
