import { useAuth, useZudoku } from "zudoku/hooks";
import { useState, useEffect, useCallback } from "react";
import { PricingPlans } from "./PricingPlans";

export function PricingPage() {
  const auth = useAuth();
  const { signRequest } = useZudoku();
  const [currentPlan, setCurrentPlan] = useState<string | undefined>(undefined);

  const fetchPlan = useCallback(async () => {
    try {
      const serverUrl =
        (typeof process !== "undefined" &&
          (process.env as any)?.ZUPLO_PUBLIC_SERVER_URL) ||
        (import.meta as any).env?.ZUPLO_SERVER_URL ||
        "";
      const req = new Request(serverUrl + "/v1/account/usage/detailed");
      const signed = await signRequest(req);
      const res = await fetch(signed);
      if (res.ok) {
        const json = await res.json();
        setCurrentPlan(json.plan?.name);
      }
    } catch {
      // If we can't fetch plan, just show cards without highlighting
    }
  }, [signRequest]);

  useEffect(() => {
    if (auth.isAuthenticated) {
      fetchPlan();
    }
  }, [auth.isAuthenticated, fetchPlan]);

  return (
    <div className="max-w-5xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
      <h1 className="font-medium text-2xl pb-2">Plans & Pricing</h1>
      <p className="text-muted-foreground mb-6">
        Choose the plan that fits your lookup volume.
      </p>
      <PricingPlans currentPlan={currentPlan} />
    </div>
  );
}
