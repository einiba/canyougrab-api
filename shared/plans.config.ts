/**
 * Single source of truth for all plan definitions.
 * Consumed by: developer portal, marketing site, backend (via JSON export), Stripe sync script.
 */

export interface PlanDefinition {
  id: string;
  name: string;
  monthlyPrice: number;
  monthlyLimit: number;
  minuteLimit: number;
  domainCap: number;
  /**
   * Hosted AI name-generation requests included per month.
   * 0 means BYOK-only — the plan does not include any hosted LLM credit.
   * Enforcement endpoint POST /api/portal/names/generate is not implemented yet,
   * so today this field is consumed only by the marketing/portal pricing UIs.
   */
  hostedLlmMonthly: number;
  features: string[];
  isActive: boolean;
  isFree: boolean;
  requiresCard: boolean;
  displayOrder: number;
  /** When true, omit from the public marketing pricing grid. The plan still
   *  exists and is rendered to logged-in users on it (e.g. the portal pricing
   *  page) so they can see what they're on. */
  hideFromMarketing?: boolean;
  badge?: string;
  note?: string;
  cta?: string;
  stripe: {
    test: { priceId: string; productId: string } | null;
    live: { priceId: string; productId: string } | null;
  };
}

export const PLANS: Record<string, PlanDefinition> = {
  free: {
    id: "free",
    name: "Free",
    monthlyPrice: 0,
    monthlyLimit: 500,
    minuteLimit: 30,
    domainCap: 30,
    hostedLlmMonthly: 0,
    features: [
      "500 lookups/month",
      "30 requests/min",
      "30 domains/request",
      "BYOK only — bring your own AI key",
    ],
    isActive: true,
    isFree: true,
    requiresCard: false,
    displayOrder: 0,
    hideFromMarketing: true,
    cta: "Start Free",
    note: "API tier — get a developer API key for evaluation. Use Verified for the Web UI.",
    stripe: { test: null, live: null },
  },
  free_plus: {
    id: "free_plus",
    name: "Verified",
    monthlyPrice: 0,
    monthlyLimit: 10_000,
    minuteLimit: 100,
    domainCap: 100,
    hostedLlmMonthly: 50,
    features: [
      "10,000 lookups/month",
      "100 requests/min",
      "100 domains/request",
      "50 AI name generations / month",
      "Card on file required",
    ],
    isActive: true,
    isFree: true,
    requiresCard: true,
    displayOrder: 1,
    stripe: { test: null, live: null },
  },
  basic: {
    id: "basic",
    name: "Basic",
    monthlyPrice: 5,
    monthlyLimit: 20_000,
    minuteLimit: 300,
    domainCap: 100,
    hostedLlmMonthly: 200,
    features: [
      "20,000 lookups/month",
      "300 requests/min",
      "100 domains/request",
      "200 AI name generations / month",
      "Email support",
    ],
    isActive: true,
    isFree: false,
    requiresCard: false,
    displayOrder: 2,
    stripe: {
      test: { priceId: "price_1TAggjH8ksFkvmqRNEE6UHx3", productId: "prod_U8yekyOyudvstr" },
      live: { priceId: "price_1TC2DvHWwGSUcGDUDgNMlRgD", productId: "prod_UAMxgjrBkyZXDR" },
    },
  },
  pro: {
    id: "pro",
    name: "Pro",
    monthlyPrice: 7,
    monthlyLimit: 50_000,
    minuteLimit: 1_000,
    domainCap: 100,
    hostedLlmMonthly: 500,
    features: [
      "50,000 lookups/month",
      "1,000 requests/min",
      "100 domains/request",
      "500 AI name generations / month",
      "Priority support",
    ],
    isActive: true,
    isFree: false,
    requiresCard: false,
    displayOrder: 3,
    badge: "Most Popular",
    stripe: {
      test: { priceId: "price_1TAggkH8ksFkvmqRUx9kVWf9", productId: "prod_U8ye2SLgAQVpiA" },
      live: { priceId: "price_1TC2DvHWwGSUcGDUqBQf1jZm", productId: "prod_UAMxnd9uzO3BYf" },
    },
  },
  business: {
    id: "business",
    name: "Business",
    monthlyPrice: 9,
    monthlyLimit: 300_000,
    minuteLimit: 3_000,
    domainCap: 100,
    hostedLlmMonthly: 2_000,
    features: [
      "300,000 lookups/month",
      "3,000 requests/min",
      "100 domains/request",
      "2,000 AI name generations / month",
      "Priority support",
    ],
    isActive: true,
    isFree: false,
    requiresCard: false,
    displayOrder: 4,
    stripe: {
      test: { priceId: "price_1TAggkH8ksFkvmqRn7c63MZE", productId: "prod_U8yeDTiT8NCe7s" },
      live: { priceId: "price_1TC2DvHWwGSUcGDUgoh8E1Kc", productId: "prod_UAMx6gMNTEsWbV" },
    },
  },
  // Retired plans — kept for reference, not displayed
  starter: {
    id: "starter",
    name: "Starter",
    monthlyPrice: 1,
    monthlyLimit: 100,
    minuteLimit: 10,
    domainCap: 100,
    hostedLlmMonthly: 0,
    features: [],
    isActive: false,
    isFree: false,
    requiresCard: false,
    displayOrder: -1,
    stripe: {
      test: { priceId: "price_1TAggjH8ksFkvmqRKVBO4YhN", productId: "prod_U8ydsSycJBla9H" },
      live: null, // Starter was never fully set up in live mode
    },
  },
};

/** Get only active plans, sorted by displayOrder */
export function getActivePlans(): PlanDefinition[] {
  return Object.values(PLANS)
    .filter((p) => p.isActive)
    .sort((a, b) => a.displayOrder - b.displayOrder);
}

/** Get active paid plans (have Stripe prices) */
export function getPaidPlans(): PlanDefinition[] {
  return getActivePlans().filter((p) => !p.isFree);
}

/** Get active plans suitable for the public pricing grid (excludes Free+ since it's an upgrade, not a standalone choice) */
export function getDisplayPlans(): PlanDefinition[] {
  return getActivePlans().filter((p) => p.id !== "free_plus");
}

/** Get plans for the marketing pricing grid — excludes plans flagged hideFromMarketing.
 *  Today this drops the bare Free plan (API-only tier) and surfaces Verified instead. */
export function getMarketingPlans(): PlanDefinition[] {
  return getActivePlans().filter((p) => !p.hideFromMarketing);
}

/** Get per-100-lookups cost string */
export function getPer100Cost(plan: PlanDefinition): string {
  if (plan.isFree) return "—";
  const cost = (plan.monthlyPrice / plan.monthlyLimit) * 100;
  return cost.toFixed(2);
}

/** Build the Stripe price-to-plan map for a given environment */
export function getStripePriceMap(env: "test" | "live"): Record<string, { name: string; limit: number }> {
  const map: Record<string, { name: string; limit: number }> = {};
  for (const plan of Object.values(PLANS)) {
    const stripe = plan.stripe[env];
    if (stripe && plan.isActive) {
      map[stripe.priceId] = { name: plan.id, limit: plan.monthlyLimit };
    }
  }
  return map;
}
