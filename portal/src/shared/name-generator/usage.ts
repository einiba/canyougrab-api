export type Tier = "curious" | "trying" | "engaged";

export interface TierLimits {
  curiousLimit: number;
  tryingLimit: number;
  fullResultCount: number;
  engagedVisibleCount: number;
  engagedCooldownMs: number;
  rollingWindowMs: number;
}

export const TIER_LIMITS: TierLimits = {
  curiousLimit: 5,
  tryingLimit: 10,
  fullResultCount: 36,
  engagedVisibleCount: 3,
  engagedCooldownMs: 30_000,
  rollingWindowMs: 7 * 24 * 60 * 60 * 1000,
};

export interface UsageState {
  count: number;
  windowStart: number;
  modalDismissedForTier: Tier | null;
  lastGeneratedAt: number;
}

const KEY = "cygi_usage_v1";
const EMPTY: UsageState = {
  count: 0,
  windowStart: 0,
  modalDismissedForTier: null,
  lastGeneratedAt: 0,
};

export function readUsage(): UsageState {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...EMPTY };
    const parsed = JSON.parse(raw) as UsageState;
    if (Date.now() - parsed.windowStart > TIER_LIMITS.rollingWindowMs) {
      return { ...EMPTY };
    }
    return parsed;
  } catch {
    return { ...EMPTY };
  }
}

export function writeUsage(state: UsageState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* ignore quota errors */
  }
}

export function incrementUsage(): UsageState {
  const cur = readUsage();
  const next: UsageState = {
    ...cur,
    count: cur.count + 1,
    windowStart: cur.windowStart || Date.now(),
    lastGeneratedAt: Date.now(),
  };
  writeUsage(next);
  return next;
}

export function dismissModalForTier(tier: Tier): void {
  const cur = readUsage();
  writeUsage({ ...cur, modalDismissedForTier: tier });
}

export function tierFor(count: number): Tier {
  if (count <= TIER_LIMITS.curiousLimit) return "curious";
  if (count <= TIER_LIMITS.tryingLimit) return "trying";
  return "engaged";
}

export function cooldownRemainingMs(state: UsageState): number {
  if (tierFor(state.count) !== "engaged") return 0;
  const elapsed = Date.now() - state.lastGeneratedAt;
  return Math.max(0, TIER_LIMITS.engagedCooldownMs - elapsed);
}
