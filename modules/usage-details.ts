import { ZuploContext, ZuploRequest, environment } from "@zuplo/runtime";

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

  const limit = 100;

  if (userConsumers.length === 0) {
    return new Response(
      JSON.stringify({
        plan: { name: "starter", lookups_limit: limit, period: "monthly" },
        usage: {
          total_lookups_this_month: 0,
          lookups_remaining: limit,
          by_key: [],
        },
      }),
      { headers: { "Content-Type": "application/json" } },
    );
  }

  // Get usage from backend for all consumer IDs
  const consumerNames = userConsumers.map((c: any) => c.name);
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
    created_at: c.createdOn,
  }));

  const totalLookups = byKey.reduce(
    (sum: number, k: any) => sum + k.lookups_this_month,
    0,
  );

  return new Response(
    JSON.stringify({
      plan: {
        name: "starter",
        lookups_limit: limit,
        period: "monthly",
      },
      usage: {
        total_lookups_this_month: totalLookups,
        lookups_remaining: Math.max(0, limit - totalLookups),
        by_key: byKey,
      },
    }),
    { headers: { "Content-Type": "application/json" } },
  );
}
