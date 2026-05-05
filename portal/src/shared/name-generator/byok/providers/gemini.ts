import { PROVIDERS, type ProviderAdapter } from "../types";
import { buildPrompt, parseBases } from "../prompt";

const BASE = "https://generativelanguage.googleapis.com/v1beta";

export const geminiAdapter: ProviderAdapter = {
  meta: PROVIDERS.gemini,

  async listModels(key: string) {
    try {
      const res = await fetch(`${BASE}/models?key=${encodeURIComponent(key)}`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        return { ok: false, error: data?.error?.message ?? `HTTP ${res.status}` };
      }
      const data = await res.json();
      const models = (data?.models ?? [])
        .filter((m: { supportedGenerationMethods?: string[] }) =>
          (m?.supportedGenerationMethods ?? []).includes("generateContent"),
        )
        .map((m: { name?: string }) => (m?.name ?? "").replace(/^models\//, ""))
        .filter((id: string) =>
          /^gemini-/.test(id) && !/-(tts|image|embedding|customtools|preview-tts)\b/.test(id),
        );
      models.sort();
      return { ok: true, models };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "Network error" };
    }
  },

  async generateBases({ key, model, description, styles, tldPref, count, anchors, exclude }) {
    const res = await fetch(
      `${BASE}/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(key)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [{
            parts: [{ text: buildPrompt({ description, styles, tldPref, count, anchors, exclude }) }],
          }],
          generationConfig: { maxOutputTokens: 1024 },
        }),
      },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data?.error?.message ?? `Gemini ${res.status}`);
    }
    const data = await res.json();
    const text = data?.candidates?.[0]?.content?.parts?.[0]?.text ?? "";
    return parseBases(text, count);
  },
};
