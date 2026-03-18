import { Button } from "zudoku/components";

const PLANS = [
  {
    name: "Free",
    key: "free",
    price: 0,
    lookups: 50,
    per100: "—",
    rateLimit: 25,
    domainCap: 25,
    cta: "Get Started Free",
    note: "Add a card to unlock 200 lookups/mo",
  },
  {
    name: "Basic",
    key: "basic",
    price: 10,
    lookups: 10_000,
    per100: "0.10",
    rateLimit: 1_000,
    domainCap: 100,
    popular: true,
  },
  {
    name: "Pro",
    key: "pro",
    price: 20,
    lookups: 50_000,
    per100: "0.04",
    rateLimit: 5_000,
    domainCap: 100,
  },
  {
    name: "Business",
    key: "business",
    price: 30,
    lookups: 300_000,
    per100: "0.01",
    rateLimit: 30_000,
    domainCap: 100,
  },
];

interface PricingPlansProps {
  currentPlan?: string;
  onSelectPlan?: (plan: string) => void;
  loadingPlan?: string | null;
}

export function PricingPlans({
  currentPlan,
  onSelectPlan,
  loadingPlan,
}: PricingPlansProps) {
  // Normalize: both "free" and "free_plus" highlight the Free column
  const normalizedCurrent = currentPlan?.toLowerCase();
  const isOnFreeTier =
    normalizedCurrent === "free" || normalizedCurrent === "free_plus";

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {PLANS.map((plan) => {
        const isCurrent =
          plan.key === "free"
            ? isOnFreeTier
            : normalizedCurrent === plan.key;
        const isLoading =
          loadingPlan?.toLowerCase() === plan.key;

        return (
          <div
            key={plan.key}
            className={`border rounded-lg p-6 flex flex-col items-center text-center relative ${
              isCurrent
                ? "border-primary ring-2 ring-primary/30 bg-primary/10"
                : ""
            } ${plan.popular && !isCurrent ? "border-primary/50" : ""}`}
          >
            {plan.popular && (
              <span className="absolute -top-3 bg-primary text-primary-foreground text-xs font-medium px-3 py-1 rounded-full">
                Most Popular
              </span>
            )}

            <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
              {plan.name}
            </p>

            <p className="text-3xl font-bold mt-3">
              {plan.price === 0 ? "Free" : `$${plan.price}`}
            </p>

            {plan.per100 !== "—" && (
              <p className="text-sm mt-4">
                ${plan.per100} per 100 lookups
              </p>
            )}
            <p className={`text-sm ${plan.per100 === "—" ? "mt-4" : "mt-2"}`}>
              {plan.rateLimit.toLocaleString()} requests / hour
            </p>
            <p className="text-sm mt-2">
              {plan.lookups.toLocaleString()} lookups / month
            </p>
            <p className="text-sm mt-2 text-muted-foreground">
              {plan.domainCap} domains / request
            </p>

            {plan.note && (
              <p className="text-xs text-muted-foreground mt-3 italic">
                {plan.note}
              </p>
            )}

            <div className="mt-auto pt-5 w-full">
              {isCurrent ? (
                <Button
                  variant="outline"
                  className="w-full border-primary text-primary cursor-default pointer-events-none"
                  disabled
                >
                  {normalizedCurrent === "free_plus"
                    ? "Current Plan (Free+)"
                    : "Current Plan"}
                </Button>
              ) : (
                <Button
                  className="w-full"
                  disabled={isLoading || !!loadingPlan}
                  onClick={() => onSelectPlan?.(plan.key)}
                >
                  {isLoading
                    ? "Redirecting..."
                    : plan.cta ?? (currentPlan ? "Switch Plan" : "Get Started")}
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
