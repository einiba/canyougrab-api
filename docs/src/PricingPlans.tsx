import { Button } from "zudoku/components";

const PLANS = [
  { name: "Starter", price: 1, lookups: 100, per100: "1.00" },
  { name: "Basic", price: 10, lookups: 10_000, per100: "0.10" },
  { name: "Pro", price: 20, lookups: 50_000, per100: "0.04" },
  { name: "Business", price: 30, lookups: 300_000, per100: "0.01" },
];

export function PricingPlans({ currentPlan }: { currentPlan?: string }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {PLANS.map((plan) => {
        const isCurrent =
          currentPlan?.toLowerCase() === plan.name.toLowerCase();

        return (
          <div
            key={plan.name}
            className={`border rounded-lg p-6 flex flex-col items-center text-center ${
              isCurrent
                ? "border-emerald-500 ring-2 ring-emerald-500/30 bg-emerald-500/10"
                : ""
            }`}
          >
            <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
              {plan.name}
            </p>

            <p className="text-3xl font-bold mt-3">${plan.price}</p>
            <p className="text-xs text-muted-foreground mt-1">
              ${plan.per100} per 100 lookups
            </p>

            <p className="text-sm mt-4">
              {plan.lookups.toLocaleString()} lookups / mo
            </p>

            <div className="mt-auto pt-5 w-full">
              {isCurrent ? (
                <Button
                  variant="outline"
                  className="w-full border-emerald-500 text-emerald-500 cursor-default pointer-events-none"
                  disabled
                >
                  Current Plan
                </Button>
              ) : (
                <Button
                  className="w-full"
                  onClick={() =>
                    window.open(
                      `mailto:support@canyougrab.it?subject=Upgrade to ${plan.name} Plan`,
                    )
                  }
                >
                  {currentPlan ? "Switch Plan" : "Get Started"}
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
