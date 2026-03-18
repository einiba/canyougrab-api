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
  features: string[];
  isActive: boolean;
  isFree: boolean;
  requiresCard: boolean;
  displayOrder: number;
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
    monthlyLimit: 50,
    minuteLimit: 30,
    domainCap: 25,
    features: [
      "50 lookups/month",
      "30 requests/min",
      "25 domains/request",
      "Add a card to unlock 200 lookups/mo",
    ],
    isActive: true,
    isFree: true,
    requiresCard: false,
    displayOrder: 0,
    cta: "Get Started Free",
    note: "Add a card to unlock 200 lookups/mo",
    stripe: { test: null, live: null },
  },
  free_plus: {
    id: "free_plus",
    name: "Free+",
    monthlyPrice: 0,
    monthlyLimit: 200,
    minuteLimit: 100,
    domainCap: 50,
    features: [
      "200 lookups/month",
      "100 requests/min",
      "50 domains/request",
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
    monthlyPrice: 10,
    monthlyLimit: 10_000,
    minuteLimit: 300,
    domainCap: 100,
    features: [
      "10,000 lookups/month",
      "300 requests/min",
      "100 domains/request",
      "Email support",
    ],
    isActive: true,
    isFree: false,
    requiresCard: false,
    displayOrder: 2,
    badge: "Most Popular",
    stripe: {
      test: { priceId: "price_1TAggjH8ksFkvmqRNEE6UHx3", productId: "prod_U8yekyOyudvstr" },
      live: { priceId: "price_1TC2DvHWwGSUcGDUDgNMlRgD", productId: "prod_UAMxgjrBkyZXDR" },
    },
  },
  pro: {
    id: "pro",
    name: "Pro",
    monthlyPrice: 20,
    monthlyLimit: 50_000,
    minuteLimit: 1_000,
    domainCap: 100,
    features: [
      "50,000 lookups/month",
      "1,000 requests/min",
      "100 domains/request",
      "Priority support",
    ],
    isActive: true,
    isFree: false,
    requiresCard: false,
    displayOrder: 3,
    stripe: {
      test: { priceId: "price_1TAggkH8ksFkvmqRUx9kVWf9", productId: "prod_U8ye2SLgAQVpiA" },
      live: { priceId: "price_1TC2DvHWwGSUcGDUqBQf1jZm", productId: "prod_UAMxnd9uzO3BYf" },
    },
  },
  business: {
    id: "business",
    name: "Business",
    monthlyPrice: 30,
    monthlyLimit: 300_000,
    minuteLimit: 3_000,
    domainCap: 100,
    features: [
      "300,000 lookups/month",
      "3,000 requests/min",
      "100 domains/request",
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
