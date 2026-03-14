import { Button } from "zudoku/components";

const PLANS = [
  { name: "Starter", price: 1, lookups: 100, per100: "1.00", rateLimit: 100 },
  { name: "Basic", price: 10, lookups: 10_000, per100: "0.10", rateLimit: 1_000 },
  { name: "Pro", price: 20, lookups: 50_000, per100: "0.04", rateLimit: 5_000 },
  { name: "Business", price: 30, lookups: 300_000, per100: "0.01", rateLimit: 30_000 },
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
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {PLANS.map((plan) => {
        const isCurrent =
          currentPlan?.toLowerCase() === plan.name.toLowerCase();
        const isLoading =
          loadingPlan?.toLowerCase() === plan.name.toLowerCase();

        return (
          <div
            key={plan.name}
            className={`border rounded-lg p-6 flex flex-col items-center text-center ${
              isCurrent
                ? "border-primary ring-2 ring-primary/30 bg-primary/10"
                : ""
            }`}
          >
            <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
              {plan.name}
            </p>

            <p className="text-3xl font-bold mt-3">${plan.price}</p>

            <p className="text-sm mt-4">
              ${plan.per100} per 100 lookups
            </p>
            <p className="text-sm mt-2">
              {plan.rateLimit.toLocaleString()} requests / hour
            </p>
            <p className="text-sm mt-2">
              {plan.lookups.toLocaleString()} lookups / month
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
              ) : (
                <Button
                  className="w-full"
                  disabled={isLoading || !!loadingPlan}
                  onClick={() => onSelectPlan?.(plan.name.toLowerCase())}
                >
                  {isLoading ? "Redirecting..." : currentPlan ? "Switch Plan" : "Get Started"}
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
