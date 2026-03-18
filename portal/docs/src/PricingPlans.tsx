import { Button } from "zudoku/components";

const PLANS = [
  {
    name: "Free",
    key: "free",
    price: 0,
    priceLabel: "Free",
    lookups: 500,
    per100: "—",
    rateLimit: 30,
    domainCap: 30,
    cta: "Start Free",
  },
  {
    name: "Verified",
    key: "free_plus",
    price: 0,
    priceLabel: "Free",
    subtitle: "Card on file",
    lookups: 10_000,
    per100: "—",
    rateLimit: 100,
    domainCap: 100,
  },
  {
    name: "Basic",
    key: "basic",
    price: 10,
    priceLabel: "$10",
    lookups: 20_000,
    per100: "0.05",
    rateLimit: 300,
    domainCap: 100,
    popular: true,
  },
  {
    name: "Pro",
    key: "pro",
    price: 20,
    priceLabel: "$20",
    lookups: 50_000,
    per100: "0.04",
    rateLimit: 1_000,
    domainCap: 100,
  },
  {
    name: "Business",
    key: "business",
    price: 30,
    priceLabel: "$30",
    lookups: 300_000,
    per100: "0.01",
    rateLimit: 3_000,
    domainCap: 100,
  },
];

interface PricingPlansProps {
  currentPlan?: string;
  onSelectPlan?: (plan: string) => void;
  onUpgradeFreePlus?: () => void;
  loadingPlan?: string | null;
  freePlusLoading?: boolean;
}

export function PricingPlans({
  currentPlan,
  onSelectPlan,
  onUpgradeFreePlus,
  loadingPlan,
  freePlusLoading,
}: PricingPlansProps) {
  const normalizedCurrent = currentPlan?.toLowerCase();

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
      {PLANS.map((plan) => {
        const isCurrent = normalizedCurrent === plan.key;
        const isLoading = loadingPlan?.toLowerCase() === plan.key;
        const isFreePlus = plan.key === "free_plus";

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

            <p className={`text-sm font-medium uppercase tracking-wide ${isFreePlus ? "text-orange-400" : "text-muted-foreground"}`}>
              {plan.name}
            </p>

            <p className="text-3xl font-bold mt-3">
              {plan.priceLabel}
            </p>

            {plan.subtitle && (
              <p className="text-xs text-orange-400 mt-1">
                {plan.subtitle}
              </p>
            )}

            {plan.per100 !== "—" && (
              <p className="text-sm mt-4">
                ${plan.per100} per 100 lookups
              </p>
            )}
            <p className={`text-sm ${plan.per100 === "—" ? "mt-4" : "mt-2"}`}>
              {plan.rateLimit.toLocaleString()} requests / min
            </p>
            <p className="text-sm mt-2">
              {plan.lookups.toLocaleString()} lookups / month
            </p>
            <p className="text-sm mt-2 text-muted-foreground">
              {plan.domainCap} domains / request
            </p>

            <div className="mt-auto pt-5 w-full">
              {isCurrent ? (
                <Button
                  variant="outline"
                  className="w-full border-primary text-primary cursor-default pointer-events-none"
                  disabled
                >
                  Current Plan
                </Button>
              ) : isFreePlus && onUpgradeFreePlus ? (
                <Button
                  className="w-full text-orange-400 border-orange-400 hover:bg-orange-400/10"
                  variant="outline"
                  disabled={freePlusLoading}
                  onClick={() => onUpgradeFreePlus()}
                >
                  {freePlusLoading ? "Setting up..." : "Unlock Free+"}
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
