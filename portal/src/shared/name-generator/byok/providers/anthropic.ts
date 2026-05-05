import { PROVIDERS, type ProviderAdapter } from "../types";
import { buildPrompt, parseBases } from "../prompt";

const API_URL = "https://api.anthropic.com/v1/messages";
const VERSION = "2023-06-01";

export const anthropicAdapter: ProviderAdapter = {
  meta: PROVIDERS.anthropic,

  async listModels(key: string) {
    try {
      const res = await fetch("https://api.anthropic.com/v1/models", {
        headers: {
          "x-api-key": key,
          "anthropic-version": VERSION,
          "anthropic-dangerous-direct-browser-access": "true",
        },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        return { ok: false, error: data?.error?.message ?? `HTTP ${res.status}` };
      }
      const data = await res.json();
      const models = (data?.data ?? [])
        .map((m: { id?: string }) => m?.id)
        .filter((id: unknown): id is string => typeof id === "string" && id.startsWith("claude-"));
      return { ok: true, models: models.sort().reverse() };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "Network error" };
    }
  },

  async generateBases({ key, model, description, styles, tldPref, count, anchors, exclude }) {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": VERSION,
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify({
        model,
        max_tokens: 1024,
        messages: [{ role: "user", content: buildPrompt({ description, styles, tldPref, count, anchors, exclude }) }],
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data?.error?.message ?? `Anthropic ${res.status}`);
    }
    const data = await res.json();
    const text = data?.content?.[0]?.text ?? "";
    return parseBases(text, count);
  },
};
