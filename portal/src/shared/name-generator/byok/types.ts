export type ProviderId = "anthropic" | "openai" | "gemini";

export interface ProviderMeta {
  id: ProviderId;
  label: string;
  consoleUrl: string;
  keyPrefix: string;
  keyHint: string;
  defaultModel: string;
  modelOptions: string[];
}

export interface ByokKey {
  provider: ProviderId;
  key: string;
  model: string;
}

export interface ProviderAdapter {
  meta: ProviderMeta;
  /**
   * Validate the key by listing models the key can use. A successful response
   * doubles as a validity check, so we don't need a separate testKey RPC.
   * Returns chat-capable model IDs (filtered to exclude embeddings, audio, etc).
   */
  listModels(key: string): Promise<{ ok: boolean; models?: string[]; error?: string }>;
  /** Generate brandable name bases. Returns lowercase alphanumeric strings. */
  generateBases(args: {
    key: string;
    model: string;
    description: string;
    styles: string[];
    tldPref: string;
    count: number;
    anchors?: string[];
    exclude?: string[];
  }): Promise<string[]>;
}

export const PROVIDERS: Record<ProviderId, ProviderMeta> = {
  anthropic: {
    id: "anthropic",
    label: "Anthropic (Claude)",
    consoleUrl: "https://console.anthropic.com/settings/keys",
    keyPrefix: "sk-ant-",
    keyHint: "Starts with sk-ant-",
    defaultModel: "claude-haiku-4-5",
    modelOptions: ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"],
  },
  openai: {
    id: "openai",
    label: "OpenAI",
    consoleUrl: "https://platform.openai.com/api-keys",
    keyPrefix: "sk-",
    keyHint: "Starts with sk- (project or user key)",
    defaultModel: "gpt-4o-mini",
    modelOptions: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
  },
  gemini: {
    id: "gemini",
    label: "Google (Gemini)",
    consoleUrl: "https://aistudio.google.com/apikey",
    keyPrefix: "",
    keyHint: "Google AI Studio key",
    defaultModel: "gemini-2.5-flash",
    modelOptions: [
      "gemini-2.5-flash",
      "gemini-2.5-pro",
      "gemini-2.0-flash",
      "gemini-flash-latest",
      "gemini-pro-latest",
    ],
  },
};
