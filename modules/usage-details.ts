import { ZuploContext, ZuploRequest, environment } from "@zuplo/runtime";
import { PLAN_LIMITS, findOrCreateCustomer, getActiveSubscription, PRICE_TO_PLAN_MAP } from "./stripe-helpers";

const accountName = environment.ZP_ACCOUNT_NAME;
const bucketName = environment.ZP_API_KEY_SERVICE_BUCKET_NAME;

export default async function (request: ZuploRequest, context: ZuploContext) {
  const sub = request.user?.sub;
  if (!sub) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Fetch all consumers from Zuplo Management API
  const consumersRes = await fetch(
    `https://dev.zuplo.com/v1/accounts/${accountName}/key-buckets/${bucketName}/consumers`,
    {
      headers: {
        Authorization: `Bearer ${environment.ZP_DEVELOPER_API_KEY}`,
      },
    },
  );

  if (!consumersRes.ok) {
    context.log.error(
      `Failed to fetch consumers: ${await consumersRes.text()}`,
    );
    return new Response(
      JSON.stringify({ error: "Failed to fetch usage data" }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }

  const consumersData = await consumersRes.json();

  // Filter consumers belonging to this user (by tags or managers)
  const userConsumers = (consumersData.data || []).filter(
    (c: any) =>
      c.tags?.sub === sub ||
      c.managers?.some((m: any) => m.sub === sub),
  );

  const email = (request.user as any)?.data?.email;

  // Read plan from consumer tags (set by Stripe webhook)
  const planName = userConsumers[0]?.tags?.plan || "none";
  const limit = planName !== "none"
    ? (PLAN_LIMITS[planName] ?? parseInt(userConsumers[0]?.tags?.lookups_limit || "0", 10))
    : 0;
  const hasSubscription = planName !== "none";

  if (userConsumers.length === 0) {
    // No consumers — check Stripe for an active subscription as fallback
    let fallbackPlan = "none";
    let fallbackLimit = 0;
    let fallbackHasSub = false;

    try {
      const customerId = await findOrCreateCustomer(sub, email);
      const activeSub = await getActiveSubscription(customerId);
      if (activeSub) {
        const priceId = activeSub.items?.data?.[0]?.price?.id;
        if (priceId && PRICE_TO_PLAN_MAP[priceId]) {
          fallbackPlan = PRICE_TO_PLAN_MAP[priceId].name;
          fallbackLimit = PRICE_TO_PLAN_MAP[priceId].limit;
          fallbackHasSub = true;
        }
      }
    } catch (err) {
      context.log.warn(`Stripe fallback lookup failed: ${err}`);
    }

    return new Response(
      JSON.stringify({
        plan: { name: fallbackPlan, lookups_limit: fallbackLimit, period: "monthly" },
        has_subscription: fallbackHasSub,
        usage: {
          total_lookups_this_month: 0,
          lookups_remaining: fallbackLimit,
          by_key: [],
        },
      }),
      { headers: { "Content-Type": "application/json" } },
    );
  }

  // Get usage from backend for all consumer IDs
  const consumerNames = userConsumers.map((c: any) => c.name);
  // Fetch monthly usage (backend now returns full month, not just today)
  const usageRes = await fetch(
    `${environment.BASE_URL}/api/account/usage/detailed`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ consumers: consumerNames }),
    },
  );

  const usageData = await usageRes.json();

  const byKey = userConsumers.map((c: any) => ({
    consumer_id: c.name,
    description: c.description || "API Key",
    lookups_this_month: usageData.by_consumer?.[c.name] || 0,
    lookups_this_hour: usageData.hourly_by_consumer?.[c.name] || 0,
    created_at: c.createdOn,
  }));

  const totalLookups = byKey.reduce(
    (sum: number, k: any) => sum + k.lookups_this_month,
    0,
  );

  const totalHourlyLookups = byKey.reduce(
    (sum: number, k: any) => sum + k.lookups_this_hour,
    0,
  );

  return new Response(
    JSON.stringify({
      plan: {
        name: planName,
        lookups_limit: limit,
        period: "monthly",
      },
      has_subscription: hasSubscription,
      usage: {
        total_lookups_this_month: totalLookups,
        total_lookups_this_hour: totalHourlyLookups,
        lookups_remaining: Math.max(0, limit - totalLookups),
        by_key: byKey,
      },
    }),
    { headers: { "Content-Type": "application/json" } },
  );
}
