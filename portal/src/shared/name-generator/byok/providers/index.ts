import type { ProviderAdapter, ProviderId } from "../types";
import { anthropicAdapter } from "./anthropic";
import { openaiAdapter } from "./openai";
import { geminiAdapter } from "./gemini";

export const PROVIDER_ADAPTERS: Record<ProviderId, ProviderAdapter> = {
  anthropic: anthropicAdapter,
  openai: openaiAdapter,
  gemini: geminiAdapter,
};

export function getAdapter(id: ProviderId): ProviderAdapter {
  return PROVIDER_ADAPTERS[id];
}
