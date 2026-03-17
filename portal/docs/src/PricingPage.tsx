import { useAuth, useZudoku } from "zudoku/hooks";
import { useState, useEffect, useCallback } from "react";
import { Button } from "zudoku/components";
import { PricingPlans } from "./PricingPlans";
import { API_BASE } from "./config.js";

export function PricingPage() {
  const auth = useAuth();
  const { signRequest } = useZudoku();
  const [currentPlan, setCurrentPlan] = useState<string | undefined>(undefined);
  const [hasSub, setHasSub] = useState(false);
  const [loadingPlan, setLoadingPlan] = useState<string | null>(null);
  const [cancelLoading, setCancelLoading] = useState(false);
  const [checkoutStatus, setCheckoutStatus] = useState<"success" | "cancel" | null>(null);

  // Check for checkout result in URL params
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("checkout");
    if (status === "success" || status === "cancel") {
      setCheckoutStatus(status);
      // Clean up URL
      const url = new URL(window.location.href);
      url.searchParams.delete("checkout");
      window.history.replaceState({}, "", url.pathname);
    }
  }, []);

  const fetchPlan = useCallback(async () => {
    try {
      const req = new Request(API_BASE + "/api/billing/usage/detailed");
      const signed = await signRequest(req);
      const res = await fetch(signed);
      if (res.ok) {
        const json = await res.json();
        setCurrentPlan(json.plan?.name);
        setHasSub(json.has_subscription ?? false);
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

  const handleSelectPlan = useCallback(
    async (plan: string) => {
      if (!auth.isAuthenticated) {
        auth.login();
        return;
      }

      // Free plan — just sign up / create key, no Stripe needed
      if (plan === "free") {
        // User is already on free by default after signup
        // Redirect to API keys page to create their first key
        window.location.href = "/settings/api-keys";
        return;
      }

      setLoadingPlan(plan);
      try {
        const req = new Request(API_BASE + "/api/billing/checkout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan }),
        });
        const signed = await signRequest(req);
        const res = await fetch(signed);
        const json = await res.json();

        if (json.url) {
          window.location.href = json.url;
        } else {
          setLoadingPlan(null);
        }
      } catch {
        setLoadingPlan(null);
      }
    },
    [auth, signRequest],
  );

  const handleCancelSubscription = useCallback(async () => {
    setCancelLoading(true);
    try {
      const req = new Request(API_BASE + "/api/billing/portal", {
        method: "POST",
      });
      const signed = await signRequest(req);
      const res = await fetch(signed);
      const json = await res.json();
      if (json.url) {
        window.location.href = json.url;
      }
    } catch {
      // Silently fail
    } finally {
      setCancelLoading(false);
    }
  }, [signRequest]);

  return (
    <div className="max-w-5xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
      <h1 className="font-medium text-2xl pb-2">Plans & Pricing</h1>
      <p className="text-muted-foreground mb-6">
        Start free. Upgrade when you need more lookups.
      </p>

      {checkoutStatus === "success" && (
        <div className="border border-primary/30 rounded-lg p-4 bg-primary/10 mb-6">
          <p className="text-primary">
            Subscription activated! Your plan will be updated shortly.
          </p>
        </div>
      )}

      {checkoutStatus === "cancel" && (
        <div className="border border-yellow-800 rounded-lg p-4 bg-yellow-950 mb-6">
          <p className="text-yellow-400">
            Checkout was cancelled. You can try again below.
          </p>
        </div>
      )}

      <PricingPlans
        currentPlan={currentPlan}
        onSelectPlan={handleSelectPlan}
        loadingPlan={loadingPlan}
      />

      <p className="text-center text-sm text-muted-foreground mt-6">
        All plans include real zone file data from ICANN, updated daily across 800+ TLDs.
      </p>

      {hasSub && (
        <div className="mt-8 text-center">
          <Button
            variant="outline"
            onClick={handleCancelSubscription}
            disabled={cancelLoading}
            className="text-muted-foreground hover:text-destructive hover:border-destructive"
          >
            {cancelLoading ? "Loading..." : "Cancel Subscription"}
          </Button>
        </div>
      )}
    </div>
  );
}
