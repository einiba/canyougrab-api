import { useState, useEffect, useCallback } from "react";
import { useAuth, useZudoku } from "zudoku/hooks";
import { Button } from "zudoku/components";
import { PricingPlans } from "./PricingPlans";

interface PlanInfo {
  name: string;
  lookups_limit: number;
  period: string;
}

interface KeyUsage {
  consumer_id: string;
  description: string;
  lookups_this_month: number;
  created_at: string;
}

interface UsageData {
  plan: PlanInfo;
  usage: {
    total_lookups_this_month: number;
    lookups_remaining: number;
    by_key: KeyUsage[];
  };
}

function getServerUrl(): string {
  return (
    (typeof process !== "undefined" &&
      (process.env as any)?.ZUPLO_PUBLIC_SERVER_URL) ||
    (import.meta as any).env?.ZUPLO_SERVER_URL ||
    ""
  );
}

function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const color =
    pct >= 90
      ? "bg-red-500"
      : pct >= 70
        ? "bg-yellow-500"
        : "bg-emerald-500";

  return (
    <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-3 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function UsageDashboard() {
  const auth = useAuth();
  const { signRequest } = useZudoku();
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchUsage = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const serverUrl = getServerUrl();
      const req = new Request(serverUrl + "/v1/account/usage/detailed");
      const signed = await signRequest(req);
      const res = await fetch(signed);
      if (!res.ok) {
        throw new Error(`Failed to load usage data (${res.status})`);
      }
      const json: UsageData = await res.json();
      setData(json);
    } catch (err: any) {
      setError(err.message || "Failed to load usage data");
    } finally {
      setLoading(false);
    }
  }, [signRequest]);

  useEffect(() => {
    if (auth.isAuthenticated) {
      fetchUsage();
    }
  }, [auth.isAuthenticated, fetchUsage]);

  if (!auth.isAuthenticated) {
    return (
      <div className="max-w-3xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
        <h1 className="font-medium text-2xl pb-3">Usage & Billing</h1>
        <p className="text-muted-foreground">
          Please sign in to view your usage data.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="max-w-3xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
        <h1 className="font-medium text-2xl pb-3">Usage & Billing</h1>
        <div className="animate-pulse space-y-4">
          <div className="h-40 bg-gray-200 dark:bg-gray-800 rounded-lg" />
          <div className="h-32 bg-gray-200 dark:bg-gray-800 rounded-lg" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-3xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
        <h1 className="font-medium text-2xl pb-3">Usage & Billing</h1>
        <div className="border border-red-300 dark:border-red-800 rounded-lg p-4 bg-red-50 dark:bg-red-950">
          <p className="text-red-700 dark:text-red-400">{error}</p>
          <Button onClick={fetchUsage} className="mt-3">
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { plan, usage } = data;
  const pct =
    plan.lookups_limit > 0
      ? Math.round((usage.total_lookups_this_month / plan.lookups_limit) * 100)
      : 0;

  return (
    <div className="max-w-3xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
      <div className="flex justify-between items-center pb-3">
        <h1 className="font-medium text-2xl">Usage & Billing</h1>
        <Button onClick={fetchUsage} variant="outline">
          Refresh
        </Button>
      </div>

      {/* Plan overview card */}
      <div className="border rounded-lg p-6 mb-6">
        <div className="flex justify-between items-start mb-4">
          <div>
            <p className="text-sm text-muted-foreground uppercase tracking-wide">
              Current Plan
            </p>
            <p className="text-xl font-semibold capitalize mt-1">
              {plan.name}
            </p>
          </div>
          <span className="text-sm text-muted-foreground">
            Resets monthly
          </span>
        </div>

        <div className="mb-2">
          <div className="flex justify-between text-sm mb-1">
            <span>
              {usage.total_lookups_this_month.toLocaleString()} /{" "}
              {plan.lookups_limit.toLocaleString()} domain lookups used
            </span>
            <span className="text-muted-foreground">{pct}%</span>
          </div>
          <ProgressBar
            value={usage.total_lookups_this_month}
            max={plan.lookups_limit}
          />
        </div>

        <p className="text-sm text-muted-foreground mt-2">
          {usage.lookups_remaining.toLocaleString()} lookups remaining this
          month
        </p>
      </div>

      {/* Per-key breakdown */}
      <h2 className="font-medium text-lg mb-3">Usage by API Key</h2>
      {usage.by_key.length === 0 ? (
        <div className="border rounded-lg p-4 text-center text-muted-foreground">
          No API keys found. Create one in the{" "}
          <a href="/settings/api-keys" className="underline">
            API Keys
          </a>{" "}
          page.
        </div>
      ) : (
        <div className="border rounded-lg divide-y">
          {usage.by_key.map((key) => (
            <div
              key={key.consumer_id}
              className="flex items-center justify-between p-4"
            >
              <div className="min-w-0 flex-1">
                <p className="font-medium truncate">{key.description}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {key.consumer_id.slice(0, 8)}...
                  {key.created_at &&
                    ` \u00B7 Created ${new Date(key.created_at).toLocaleDateString()}`}
                </p>
              </div>
              <div className="text-right ml-4 shrink-0">
                <p className="font-semibold tabular-nums">
                  {key.lookups_this_month.toLocaleString()}
                </p>
                <p className="text-xs text-muted-foreground">
                  lookups this month
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Plan options */}
      <h2 className="font-medium text-lg mb-3 mt-8">Plans</h2>
      <PricingPlans currentPlan={plan.name} />
    </div>
  );
}
