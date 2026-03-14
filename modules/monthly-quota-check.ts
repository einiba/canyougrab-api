import { ZuploContext, ZuploRequest, environment } from "@zuplo/runtime";
import { PLAN_LIMITS } from "./stripe-helpers";

const PLAN_HOURLY_LIMITS: Record<string, number> = {
  starter: 100,
  basic: 1_000,
  pro: 5_000,
  business: 30_000,
};

export default async function (
  request: ZuploRequest,
  context: ZuploContext,
  options: Record<string, unknown>,
  policyName: string,
) {
  const consumer = request.user?.sub;
  if (!consumer) {
    return request;
  }

  const plan =
    (request.user as any)?.data?.plan?.toLowerCase() ?? "";
  const monthlyLimit = PLAN_LIMITS[plan] ?? 0;
  const hourlyLimit = PLAN_HOURLY_LIMITS[plan] ?? 0;

  if (monthlyLimit === 0) {
    return new Response(
      JSON.stringify({
        error: "No active subscription",
        message:
          "You do not have an active subscription. Please subscribe to a plan at https://portal.canyougrab.it/pricing",
      }),
      {
        status: 403,
        headers: { "Content-Type": "application/json" },
      },
    );
  }

  try {
    const res = await fetch(
      `${environment.BASE_URL}/api/account/quota-check`,
      {
        headers: { "x-consumer": consumer },
      },
    );

    if (!res.ok) {
      context.log.warn(
        `Quota check backend returned ${res.status}`,
      );
      return request;
    }

    const data = await res.json();
    const monthlyLookups = data.monthly_lookups ?? 0;
    const hourlyLookups = data.hourly_lookups ?? 0;

    if (monthlyLookups >= monthlyLimit) {
      return new Response(
        JSON.stringify({
          error: "Monthly quota exceeded",
          message: `You have used ${monthlyLookups.toLocaleString()} of your ${monthlyLimit.toLocaleString()} monthly domain lookups on the ${plan.charAt(0).toUpperCase() + plan.slice(1)} plan. Your quota resets at the beginning of next month. Upgrade your plan at https://portal.canyougrab.it/pricing`,
          usage: {
            monthly_lookups: monthlyLookups,
            monthly_limit: monthlyLimit,
            plan,
          },
        }),
        {
          status: 429,
          headers: {
            "Content-Type": "application/json",
            "Retry-After": "3600",
          },
        },
      );
    }

    if (hourlyLimit > 0 && hourlyLookups >= hourlyLimit) {
      return new Response(
        JSON.stringify({
          error: "Hourly lookup limit exceeded",
          message: `You have used ${hourlyLookups.toLocaleString()} of your ${hourlyLimit.toLocaleString()} hourly domain lookups on the ${plan.charAt(0).toUpperCase() + plan.slice(1)} plan. Your hourly limit resets at the top of the next UTC hour. Upgrade your plan for higher limits at https://portal.canyougrab.it/pricing`,
          usage: {
            hourly_lookups: hourlyLookups,
            hourly_limit: hourlyLimit,
            plan,
          },
        }),
        {
          status: 429,
          headers: {
            "Content-Type": "application/json",
            "Retry-After": "3600",
          },
        },
      );
    }
  } catch (err) {
    context.log.error(`Quota check failed: ${err}`);
    // Fail open — don't block requests if quota check is unavailable
  }

  return request;
}
