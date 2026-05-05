import type { ByokKey, ProviderId } from "./types";

const STORAGE_KEY = "cygi_byok_v1";

/**
 * BYOK keys live in sessionStorage only. They are erased when the tab closes.
 * Keys are stored per-provider so switching providers in the settings modal
 * does not discard previously-pasted keys. The "active" provider determines
 * which key the name generator uses for the next call.
 */

interface ByokState {
  active: ProviderId | null;
  keys: Partial<Record<ProviderId, { key: string; model: string }>>;
}

const EMPTY: ByokState = { active: null, keys: {} };

const subscribers = new Set<() => void>();

function notify() {
  for (const fn of subscribers) {
    try { fn(); } catch { /* ignore */ }
  }
}

export function subscribe(listener: () => void): () => void {
  subscribers.add(listener);
  return () => subscribers.delete(listener);
}

function readState(): ByokState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...EMPTY };
    const parsed = JSON.parse(raw);
    // Legacy single-key shape: { provider, key, model } — migrate.
    if (parsed && typeof parsed === "object" && parsed.provider && parsed.key) {
      const provider = parsed.provider as ProviderId;
      return {
        active: provider,
        keys: { [provider]: { key: parsed.key, model: parsed.model ?? "" } },
      };
    }
    if (parsed && typeof parsed === "object" && parsed.keys) {
      return {
        active: (parsed.active as ProviderId | null) ?? null,
        keys: parsed.keys ?? {},
      };
    }
    return { ...EMPTY };
  } catch {
    return { ...EMPTY };
  }
}

function writeState(state: ByokState): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    notify();
  } catch {
    /* sessionStorage may be disabled */
  }
}

/** Returns the currently-active key, or null if none. */
export function readByokKey(): ByokKey | null {
  const state = readState();
  if (!state.active) return null;
  const entry = state.keys[state.active];
  if (!entry || !entry.key) return null;
  return { provider: state.active, key: entry.key, model: entry.model };
}

/** Returns the saved key for a specific provider (without changing active), or null. */
export function readKeyForProvider(provider: ProviderId): { key: string; model: string } | null {
  const state = readState();
  const entry = state.keys[provider];
  return entry && entry.key ? { ...entry } : null;
}

/** Saves a key for the given provider AND marks it active. */
export function writeByokKey(value: ByokKey): void {
  const state = readState();
  state.keys[value.provider] = { key: value.key, model: value.model };
  state.active = value.provider;
  writeState(state);
}

/**
 * Clears the active provider's key only, leaving other providers' keys intact.
 * If no provider is given, clears the active one.
 */
export function clearByokKey(provider?: ProviderId): void {
  const state = readState();
  const target = provider ?? state.active;
  if (!target) return;
  delete state.keys[target];
  if (state.active === target) {
    // Pick another saved provider as active, if any; else null.
    const remaining = Object.keys(state.keys) as ProviderId[];
    state.active = remaining[0] ?? null;
  }
  writeState(state);
}

export function clearAllByokKeys(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
    notify();
  } catch {
    /* ignore */
  }
}

export function hasByokKey(provider?: ProviderId): boolean {
  const k = readByokKey();
  if (!k) return false;
  return provider ? k.provider === provider : true;
}

export function maskKey(key: string): string {
  if (!key) return "";
  if (key.length <= 8) return "•".repeat(key.length);
  return `${key.slice(0, 4)}…${key.slice(-4)}`;
}
