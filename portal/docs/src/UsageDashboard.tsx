import { useState, useEffect, useCallback } from "react";
import { useAuth, useZudoku } from "zudoku/hooks";
import { Button } from "zudoku/components";
import { API_BASE } from "./config.js";

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
  has_subscription: boolean;
  usage: {
    total_lookups_this_month: number;
    total_lookups_this_minute: number;
    lookups_remaining: number;
    by_key: KeyUsage[];
  };
}

const PLAN_RATE_LIMITS: Record<string, number> = {
  free: 30,
  free_plus: 100,
  basic: 300,
  pro: 1_000,
  business: 3_000,
};

const PLAN_DISPLAY_NAMES: Record<string, string> = {
  free: "Free",
  free_plus: "Free+",
  basic: "Basic",
  pro: "Pro",
  business: "Business",
};

function useCountdownToNextMinute() {
  const [secondsLeft, setSecondsLeft] = useState(() => {
    const now = new Date();
    return 60 - now.getUTCSeconds();
  });

  useEffect(() => {
    const interval = setInterval(() => {
      const now = new Date();
      setSecondsLeft(60 - now.getUTCSeconds());
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  return `${secondsLeft}s`;
}

function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const color =
    pct >= 90
      ? "bg-red-500"
      : pct >= 70
        ? "bg-yellow-500"
        : "bg-primary";

  return (
    <div className="w-full bg-gray-700 rounded-full h-3 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function MinuteQuotaBar({
  planName,
  minuteUsage,
}: {
  planName: string;
  minuteUsage: number;
}) {
  const countdown = useCountdownToNextMinute();
  const limit = PLAN_RATE_LIMITS[planName.toLowerCase()] ?? 0;

  if (limit === 0) return null;

  const displayUsed = Math.min(minuteUsage, limit);
  const minutePct = Math.min(100, Math.round((minuteUsage / limit) * 100));

  return (
    <div className="mt-4 pt-4 border-t">
      <div className="flex justify-between items-start mb-2">
        <p className="text-sm font-medium">Per-Minute Rate Limit</p>
        <span className="text-sm text-muted-foreground">
          Resets in {countdown}
        </span>
      </div>

      <div className="mb-2">
        <div className="flex justify-between text-sm mb-1">
          <span>
            {displayUsed.toLocaleString()} /{" "}
            {limit.toLocaleString()} lookups this minute
          </span>
          <span className="text-muted-foreground">{minutePct}%</span>
        </div>
        <ProgressBar value={displayUsed} max={limit} />
      </div>

      <p className="text-xs text-muted-foreground mt-1">
        Rate limits reset at the top of each UTC minute for all users.
        If you exceed your limit, API responses will include a{" "}
        <code className="text-xs bg-gray-800 px-1 rounded">429</code>{" "}
        status code.
      </p>
    </div>
  );
}

function FreePlusUpgradeBanner({ onUpgrade, loading }: { onUpgrade: () => void; loading: boolean }) {
  return (
    <div className="border border-primary/30 rounded-lg p-4 bg-primary/5 mb-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium">Unlock more free lookups</p>
          <p className="text-sm text-muted-foreground mt-1">
            Add a card on file (no charge) to upgrade to Free+ with 200 lookups/month,
            100 requests/min, and 50 domains per request.
          </p>
        </div>
        <Button onClick={onUpgrade} disabled={loading} className="ml-4 shrink-0">
          {loading ? "Setting up..." : "Add Card"}
        </Button>
      </div>
    </div>
  );
}

export function UsageDashboard() {
  const auth = useAuth();
  const { signRequest } = useZudoku();
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [billingLoading, setBillingLoading] = useState(false);
  const [cardLoading, setCardLoading] = useState(false);

  const fetchUsage = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const serverUrl = API_BASE;
      const req = new Request(serverUrl + "/api/billing/usage/detailed");
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

  const handleManageBilling = useCallback(async () => {
    setBillingLoading(true);
    try {
      const serverUrl = API_BASE;
      const req = new Request(serverUrl + "/api/billing/portal", {
        method: "POST",
      });
      const signed = await signRequest(req);
      const res = await fetch(signed);
      const json = await res.json();
      if (json.url) {
        window.location.href = json.url;
      }
    } catch {
      // Silently fail — user can retry
    } finally {
      setBillingLoading(false);
    }
  }, [signRequest]);

  const handleSetupCard = useCallback(async () => {
    setCardLoading(true);
    try {
      const req = new Request(API_BASE + "/api/billing/setup-card", {
        method: "POST",
      });
      const signed = await signRequest(req);
      const res = await fetch(signed);
      const json = await res.json();

      if (json.url) {
        window.location.href = json.url;
      } else {
        setCardLoading(false);
      }
    } catch {
      setCardLoading(false);
    }
  }, [signRequest]);

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
          <div className="h-40 bg-gray-800 rounded-lg" />
          <div className="h-32 bg-gray-800 rounded-lg" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-3xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
        <h1 className="font-medium text-2xl pb-3">Usage & Billing</h1>
        <div className="border border-red-800 rounded-lg p-4 bg-red-950">
          <p className="text-red-400">{error}</p>
          <Button onClick={fetchUsage} className="mt-3">
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { plan, usage, has_subscription: hasSub } = data;
  const planKey = plan.name.toLowerCase();
  const isFreeTier = planKey === "free" || planKey === "free_plus";
  const isBasicFree = planKey === "free"; // eligible for Free+ upgrade
  const displayName = PLAN_DISPLAY_NAMES[planKey] ?? plan.name;

  const isOverLimit = usage.total_lookups_this_month > plan.lookups_limit;
  const displayUsed = isOverLimit ? plan.lookups_limit : usage.total_lookups_this_month;
  const displayRemaining = isOverLimit ? 0 : usage.lookups_remaining;
  const pct =
    plan.lookups_limit > 0
      ? Math.min(100, Math.round((usage.total_lookups_this_month / plan.lookups_limit) * 100))
      : 0;

  return (
    <div className="max-w-3xl pt-(--padding-content-top) pb-(--padding-content-bottom)">
      <div className="flex justify-between items-center pb-3">
        <h1 className="font-medium text-2xl">Usage & Billing</h1>
        <div className="flex gap-2">
          {hasSub && (
            <Button
              onClick={handleManageBilling}
              variant="outline"
              disabled={billingLoading}
            >
              {billingLoading ? "Loading..." : "Manage Billing"}
            </Button>
          )}
          <Button onClick={fetchUsage} variant="outline">
            Refresh
          </Button>
        </div>
      </div>

      {/* Free+ upgrade banner — only for users on basic Free plan */}
      {isBasicFree && (
        <FreePlusUpgradeBanner onUpgrade={handleSetupCard} loading={cardLoading} />
      )}

      {/* Upgrade CTA for free tier users */}
      {isFreeTier && !isBasicFree && (
        <div className="border border-primary/20 rounded-lg p-4 bg-primary/5 mb-6">
          <p className="text-sm text-muted-foreground">
            You're on the Free+ plan.{" "}
            <a href="/pricing" className="text-primary underline font-medium">
              Upgrade to Basic
            </a>{" "}
            for 10,000 lookups/month and 100 domains per request.
          </p>
        </div>
      )}

      {/* Plan overview card */}
      <div className="border rounded-lg p-6 mb-6">
        <div className="flex justify-between items-start mb-4">
          <div>
            <p className="text-sm text-muted-foreground uppercase tracking-wide">
              Current Plan
            </p>
            <p className="text-xl font-semibold mt-1">
              {displayName}
              <a href="/pricing" className="text-sm text-primary font-normal ml-3 hover:underline">
                {isFreeTier ? "Upgrade" : "Change plan"}
              </a>
            </p>
          </div>
          <span className="text-sm text-muted-foreground">
            Resets monthly
          </span>
        </div>

        <div className="mb-2">
          <div className="flex justify-between text-sm mb-1">
            <span>
              {displayUsed.toLocaleString()} /{" "}
              {plan.lookups_limit.toLocaleString()} domain lookups used
            </span>
            <span className="text-muted-foreground">{pct}%</span>
          </div>
          <ProgressBar
            value={displayUsed}
            max={plan.lookups_limit}
          />
        </div>

        <p className="text-sm text-muted-foreground mt-2">
          {displayRemaining.toLocaleString()} lookups remaining this
          month
        </p>

        <MinuteQuotaBar planName={plan.name} minuteUsage={usage.total_lookups_this_minute ?? 0} />
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

    </div>
  );
}
