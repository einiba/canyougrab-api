import { API_BASE_URL, PORTAL_URL } from "@/config";
import { logger } from "@/lib/logger";
import { getVisitorHeaders } from "./visitor";
import {
  TIER_LIMITS,
  incrementUsage,
  readUsage,
  tierFor,
  type Tier,
  type UsageState,
} from "./usage";
import { readByokKey } from "./byok/storage";
import { getAdapter } from "./byok/providers";
import { withVisitorId } from "./visitor";

export type NameStyle = "modern" | "playful" | "professional" | "short" | "wordplay" | "compound";
export type TldPreference = "com_only" | "tech" | "global" | "any";

export interface GenerateNamesRequest {
  description: string;
  styles?: NameStyle[];
  tldPreference?: TldPreference;
  count?: number;
  /** Bases the user liked — biases the LLM toward similar style. */
  anchors?: string[];
  /** Bases already shown — the LLM is told not to repeat them. */
  exclude?: string[];
}

export interface GeneratedName {
  domain: string;
  available: boolean | null;
  rationale?: string;
  tld: string;
  base: string;
  locked?: boolean;
}

export type GenerationMode = "hosted" | "byok";

export interface GenerateNamesResponse {
  results: GeneratedName[];
  description: string;
  shareId?: string;
  listId?: string;
  tier: Tier;
  generationsUsed: number;
  generationsLimit: number;
  signupUrl: string;
  cooldownMs: number;
  mode: GenerationMode;
  providerLabel?: string;
}

export class TrialExhaustedError extends Error {
  signupUrl: string;
  retryAfterMs: number;
  constructor(signupUrl: string, retryAfterMs: number) {
    super("Free trial limit reached");
    this.signupUrl = signupUrl;
    this.retryAfterMs = retryAfterMs;
  }
}

/**
 * Server returned 429 from /api/names/check — the BYOK visitor has hit the
 * daily availability-check cap. Surfaces a graceful soft paywall in the UI.
 */
export class ByokLimitError extends Error {
  signupUrl: string;
  dailyLimit: number;
  constructor(signupUrl: string, dailyLimit: number) {
    super("BYOK daily limit reached");
    this.signupUrl = signupUrl;
    this.dailyLimit = dailyLimit;
  }
}

const TLD_BUCKETS: Record<TldPreference, string[]> = {
  com_only: ["com"],
  tech: ["io", "dev", "ai", "app"],
  global: ["co", "net", "org", "com"],
  any: ["com", "io", "co", "ai", "dev", "app", "xyz"],
};

const DEMO_KEY = import.meta.env.VITE_DEMO_API_KEY ?? "";
const NAMEGEN_PATH = "/api/names/generate";
const CHECK_PATH = "/api/names/check";

// Higher caps for BYOK callers — they aren't burning our LLM budget, only
// our availability engine. Tunable.
const BYOK_DAILY_SOFT_LIMIT = 50;
const BYOK_NUDGE_AT = 10;

export async function generateNames(req: GenerateNamesRequest): Promise<GenerateNamesResponse> {
  const byok = readByokKey();
  if (byok && byok.key) {
    return generateWithByok(req, byok);
  }

  // BYOK is required to generate. The UI gates submission, but throw a clear
  // error if anything calls this without a key as defense in depth.
  throw new Error("Add your AI key (Anthropic, OpenAI, or Google AI Studio) to generate names.");

  // Hosted-mode generation is currently disabled — kept commented for future
  // re-enablement when we add a paid hosted tier.
  // eslint-disable-next-line no-unreachable
  const visitorHeaders = await getVisitorHeaders();

  try {
    const res = await fetch(`${API_BASE_URL}${NAMEGEN_PATH}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...visitorHeaders,
        ...(DEMO_KEY ? { Authorization: `Bearer ${DEMO_KEY}` } : {}),
      },
      body: JSON.stringify({
        description: req.description,
        styles: req.styles ?? [],
        tld_preference: req.tldPreference ?? "any",
        count: req.count ?? TIER_LIMITS.fullResultCount,
      }),
    });

    if (res.status === 404 || res.status === 501) {
      logger.warn("Name generation endpoint not yet implemented; using local fallback");
      return mockGenerate(req);
    }

    if (res.status === 429) {
      const data = await res.json().catch(() => ({}));
      throw new TrialExhaustedError(
        withVisitorId(data?.signup_url ?? `${PORTAL_URL}/signup`),
        Number(data?.retry_after_ms ?? 0),
      );
    }

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const detail = data?.detail ?? data?.message ?? `HTTP ${res.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }

    const raw = (await res.json()) as GenerateNamesResponse & { list_id?: string };
    return { ...raw, listId: raw.listId ?? raw.list_id, mode: raw.mode ?? "hosted" };
  } catch (err) {
    if (err instanceof TrialExhaustedError) throw err;
    if (err instanceof TypeError) {
      logger.warn("Name generation network error; falling back to mock", { error: String(err) });
      return mockGenerate(req);
    }
    throw err;
  }
}

async function generateWithByok(
  req: GenerateNamesRequest,
  byok: { provider: import("./byok/types").ProviderId; key: string; model: string },
): Promise<GenerateNamesResponse> {
  const adapter = getAdapter(byok.provider);
  const tldPref = req.tldPreference ?? "any";
  const styles = req.styles ?? [];
  const fullCount = req.count ?? TIER_LIMITS.fullResultCount;

  let bases: string[] = [];
  try {
    bases = await adapter.generateBases({
      key: byok.key,
      model: byok.model || adapter.meta.defaultModel,
      description: req.description,
      styles: styles.map(String),
      tldPref,
      count: 18,
      anchors: req.anchors,
      exclude: req.exclude,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Provider call failed";
    throw new Error(`Your ${adapter.meta.label} key didn't work: ${msg}`);
  }

  const tlds = TLD_BUCKETS[tldPref];
  const domains: string[] = [];
  for (const base of bases) {
    for (const tld of tlds) {
      if (domains.length >= fullCount) break;
      domains.push(`${base}.${tld}`);
    }
    if (domains.length >= fullCount) break;
  }

  const checked = await fetchAvailability(domains);
  const byDomain = new Map(checked.map((r) => [r.domain, r] as const));

  const results: GeneratedName[] = domains.map((d) => {
    const r = byDomain.get(d);
    const dot = d.lastIndexOf(".");
    return {
      domain: d,
      available: r?.available ?? null,
      tld: dot >= 0 ? d.slice(dot + 1) : "",
      base: dot >= 0 ? d.slice(0, dot) : d,
    };
  });

  results.sort((a, b) => {
    const score = (r: GeneratedName) =>
      r.available === true ? 0 : r.available === null ? 1 : 2;
    const s = score(a) - score(b);
    return s !== 0 ? s : a.domain.length - b.domain.length;
  });

  // BYOK uses a separate counter purely for soft-nudge timing; lives in localStorage.
  const used = bumpByokCounter();

  return {
    results,
    description: req.description,
    tier: "curious",
    generationsUsed: used,
    generationsLimit: BYOK_DAILY_SOFT_LIMIT,
    signupUrl: withVisitorId(`${PORTAL_URL}/signup`),
    cooldownMs: 0,
    mode: "byok",
    providerLabel: adapter.meta.label,
  };
}

interface CheckResultRow {
  domain: string;
  available: boolean | null;
}

async function fetchAvailability(domains: string[]): Promise<CheckResultRow[]> {
  if (domains.length === 0) return [];
  const visitorHeaders = await getVisitorHeaders();
  try {
    const res = await fetch(`${API_BASE_URL}${CHECK_PATH}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...visitorHeaders,
      },
      body: JSON.stringify({ domains, mode: "byok" }),
    });
    if (res.status === 404 || res.status === 501) {
      logger.warn("Availability-only endpoint not yet implemented; faking results");
      return domains.map((d) => ({ domain: d, available: pseudoAvailable(d) }));
    }
    if (res.status === 429) {
      const data = await res.json().catch(() => ({}));
      throw new ByokLimitError(
        data?.signup_url ?? `${PORTAL_URL}/signup`,
        Number(data?.daily_limit ?? BYOK_DAILY_SOFT_LIMIT),
      );
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const detail = data?.detail ?? data?.message ?? `HTTP ${res.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const data = await res.json();
    return data.results as CheckResultRow[];
  } catch (err) {
    if (err instanceof ByokLimitError) throw err;
    if (err instanceof TypeError) {
      logger.warn("Availability check network error; faking results", { error: String(err) });
      return domains.map((d) => ({ domain: d, available: pseudoAvailable(d) }));
    }
    throw err;
  }
}

const BYOK_COUNTER_KEY = "cygi_byok_count_v1";
function bumpByokCounter(): number {
  try {
    const raw = localStorage.getItem(BYOK_COUNTER_KEY);
    const today = new Date().toISOString().slice(0, 10);
    let state = { date: today, count: 0 };
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed?.date === today) state = parsed;
    }
    state.count += 1;
    localStorage.setItem(BYOK_COUNTER_KEY, JSON.stringify(state));
    return state.count;
  } catch {
    return 1;
  }
}

export const BYOK_TUNING = { BYOK_NUDGE_AT, BYOK_DAILY_SOFT_LIMIT };

function mockGenerate(req: GenerateNamesRequest): GenerateNamesResponse {
  const before = readUsage();
  const cooldownLeft = before.lastGeneratedAt
    ? Math.max(0, TIER_LIMITS.engagedCooldownMs - (Date.now() - before.lastGeneratedAt))
    : 0;
  if (tierFor(before.count) === "engaged" && cooldownLeft > 0) {
    return assembleMock(req, before, cooldownLeft, true);
  }

  const state = incrementUsage();
  return assembleMock(req, state, 0, false);
}

function assembleMock(
  req: GenerateNamesRequest,
  state: UsageState,
  cooldownMs: number,
  cachedReplay: boolean,
): GenerateNamesResponse {
  const tier = tierFor(state.count);
  const tlds = TLD_BUCKETS[req.tldPreference ?? "any"];
  const tokens = extractTokens(req.description);
  const bases = buildBases(tokens, req.styles ?? []);
  const limit = TIER_LIMITS.fullResultCount;
  const all: GeneratedName[] = [];

  for (const base of bases) {
    for (const tld of tlds) {
      if (all.length >= limit) break;
      const domain = `${base}.${tld}`;
      all.push({
        domain,
        available: pseudoAvailable(domain),
        tld,
        base,
        rationale: rationaleFor(base, req.styles ?? []),
      });
    }
    if (all.length >= limit) break;
  }

  const visible = tier === "engaged" ? TIER_LIMITS.engagedVisibleCount : all.length;
  const results = all.map((r, i) => (i < visible ? r : { ...r, locked: true }));

  return {
    results,
    description: req.description,
    listId: `mock-${state.count}`,
    tier,
    generationsUsed: state.count,
    generationsLimit:
      tier === "curious"
        ? TIER_LIMITS.curiousLimit
        : tier === "trying"
        ? TIER_LIMITS.tryingLimit
        : TIER_LIMITS.tryingLimit,
    signupUrl: withVisitorId(`${PORTAL_URL}/signup`),
    cooldownMs: cachedReplay ? cooldownMs : tier === "engaged" ? TIER_LIMITS.engagedCooldownMs : 0,
    mode: "hosted",
  };
}

const STOPWORDS = new Set([
  "a", "an", "and", "the", "for", "to", "of", "in", "on", "with", "that",
  "this", "is", "it", "we", "our", "i", "my", "be", "by", "as", "at", "or",
  "from", "but", "are", "was", "were", "will", "would", "can", "could",
  "should", "have", "has", "had", "you", "your", "they", "their", "them",
  "app", "platform", "service", "company", "business", "startup",
]);

function extractTokens(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length >= 3 && !STOPWORDS.has(t))
    .slice(0, 8);
}

function buildBases(tokens: string[], styles: NameStyle[]): string[] {
  const bases = new Set<string>();
  const playful = styles.includes("playful");
  const short = styles.includes("short");
  const compound = styles.includes("compound");
  const wordplay = styles.includes("wordplay");

  for (const t of tokens) {
    bases.add(t);
    if (short) bases.add(t.slice(0, Math.max(4, Math.floor(t.length * 0.7))));
    if (playful) {
      bases.add(`${t}ly`);
      bases.add(`${t}oo`);
      bases.add(`get${t}`);
    }
    if (wordplay) {
      bases.add(t.replace(/s$/, "z"));
      bases.add(`${t}ify`);
    }
  }

  if (compound || tokens.length >= 2) {
    for (let i = 0; i < tokens.length; i++) {
      for (let j = 0; j < tokens.length; j++) {
        if (i === j) continue;
        bases.add(`${tokens[i]}${tokens[j]}`);
      }
    }
  }

  const PREFIXES = ["use", "try", "join", "go"];
  const SUFFIXES = ["hq", "labs", "io", "stack", "kit", "hub"];
  for (const t of tokens.slice(0, 3)) {
    for (const p of PREFIXES) bases.add(`${p}${t}`);
    for (const s of SUFFIXES) bases.add(`${t}${s}`);
  }

  return Array.from(bases).filter((b) => b.length >= 3 && b.length <= 20);
}

function pseudoAvailable(domain: string): boolean | null {
  let h = 0;
  for (let i = 0; i < domain.length; i++) h = (h * 31 + domain.charCodeAt(i)) | 0;
  const v = Math.abs(h) % 10;
  if (v < 6) return true;
  if (v < 9) return false;
  return null;
}

function rationaleFor(base: string, styles: NameStyle[]): string {
  if (styles.includes("playful")) return `Playful spin on "${base}"`;
  if (styles.includes("short")) return `Short and memorable`;
  if (base.length <= 6) return `Short, brandable`;
  return `Direct and descriptive`;
}
