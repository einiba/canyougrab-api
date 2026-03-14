import { ZuploContext, ZuploRequest, CustomRateLimitDetails } from "@zuplo/runtime";

const PLAN_RATE_LIMITS: Record<string, number> = {
  starter: 100,
  basic: 1_000,
  pro: 5_000,
  business: 30_000,
};

const DEFAULT_RATE_LIMIT = 100;

export function rateLimitByPlan(
  request: ZuploRequest,
  context: ZuploContext,
): CustomRateLimitDetails {
  const plan =
    (request.user as any)?.data?.plan?.toLowerCase() ?? "";
  const limit = PLAN_RATE_LIMITS[plan] ?? DEFAULT_RATE_LIMIT;

  return {
    key: request.user?.sub ?? request.headers.get("x-forwarded-for") ?? "anonymous",
    requestsAllowed: limit,
    timeWindowMinutes: 60,
  };
}
