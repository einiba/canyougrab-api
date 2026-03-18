import { Button } from "@/components/Button";
import { getActivePlans, getPer100Cost, type PlanDefinition } from "@shared/plans.config";

export interface ApiPlan {
  name: string;
  display_name: string;
  price_cents: number;
  monthly_limit: number;
  minute_limit: number;
  domain_cap: number;
  requires_card: boolean;
  sort_order: number;
}

function apiPlanToDefinition(p: ApiPlan): PlanDefinition {
  const monthlyPrice = p.price_cents / 100;
  return {
    id: p.name,
    name: p.display_name,
    monthlyPrice,
    monthlyLimit: p.monthly_limit,
    minuteLimit: p.minute_limit,
    domainCap: p.domain_cap,
    features: [],
    isActive: true,
    isFree: monthlyPrice === 0,
    requiresCard: p.requires_card,
    displayOrder: p.sort_order,
    stripe: { test: null, live: null },
  };
}

interface PricingPlansProps {
  currentPlan?: string;
  onSelectPlan?: (plan: string) => void;
  onUpgradeFreePlus?: () => void;
  loadingPlan?: string | null;
  freePlusLoading?: boolean;
  apiPlans?: ApiPlan[];
}

export function PricingPlans({
  currentPlan,
  onSelectPlan,
  onUpgradeFreePlus,
  loadingPlan,
  freePlusLoading,
  apiPlans,
}: PricingPlansProps) {
  const normalizedCurrent = currentPlan?.toLowerCase();
  const plans = apiPlans
    ? apiPlans.map(apiPlanToDefinition)
    : getActivePlans();

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
      {plans.map((plan: PlanDefinition) => {
        const isCurrent = normalizedCurrent === plan.id;
        const isLoading = loadingPlan?.toLowerCase() === plan.id;
        const isFreePlus = plan.id === "free_plus";
        const per100 = getPer100Cost(plan);

        return (
          <div
            key={plan.id}
            className={`border rounded-lg p-6 flex flex-col items-center text-center relative ${
              isCurrent
                ? "border-primary ring-2 ring-primary/30 bg-primary/10"
                : ""
            } ${plan.badge && !isCurrent ? "border-primary/50" : ""}`}
          >
            {plan.badge && (
              <span className="absolute -top-3 bg-primary text-primary-foreground text-xs font-medium px-3 py-1 rounded-full">
                {plan.badge}
              </span>
            )}

            <p className={`text-sm font-medium uppercase tracking-wide ${isFreePlus ? "text-orange-400" : "text-muted-foreground"}`}>
              {plan.name}
            </p>

            <p className="text-3xl font-bold mt-3">
              {plan.monthlyPrice === 0 ? "Free" : `$${plan.monthlyPrice}`}
            </p>

            {plan.requiresCard && plan.isFree && (
              <p className="text-xs text-orange-400 mt-1">
                Card on file
              </p>
            )}

            <p className="text-sm mt-4">
              {plan.minuteLimit.toLocaleString()} requests / min
            </p>
            <p className="text-sm mt-2">
              {plan.monthlyLimit.toLocaleString()} lookups / month
            </p>
            {per100 !== "\u2014" && (
              <p className="text-sm mt-2 text-muted-foreground">
                ${per100} per 100 lookups
              </p>
            )}

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
                  onClick={() => onSelectPlan?.(plan.id)}
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
