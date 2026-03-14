import { ZuploContext, ZuploRequest, environment } from "@zuplo/runtime";
import { PRICE_TO_PLAN_MAP, verifyWebhookSignature } from "./stripe-helpers";

const accountName = environment.ZP_ACCOUNT_NAME;
const bucketName = environment.ZP_API_KEY_SERVICE_BUCKET_NAME;

async function updateConsumerPlan(
  auth0Sub: string,
  planName: string,
  lookupsLimit: number,
  context: ZuploContext,
) {
  // Fetch all consumers
  const consumersRes = await fetch(
    `https://dev.zuplo.com/v1/accounts/${accountName}/key-buckets/${bucketName}/consumers`,
    {
      headers: {
        Authorization: `Bearer ${environment.ZP_DEVELOPER_API_KEY}`,
      },
    },
  );

  if (!consumersRes.ok) {
    context.log.error(`Failed to fetch consumers: ${await consumersRes.text()}`);
    return;
  }

  const consumersData = await consumersRes.json();
  const userConsumers = (consumersData.data || []).filter(
    (c: any) =>
      c.tags?.sub === auth0Sub ||
      c.managers?.some((m: any) => m.sub === auth0Sub),
  );

  // Update each consumer's tags with the new plan
  for (const consumer of userConsumers) {
    const updateRes = await fetch(
      `https://dev.zuplo.com/v1/accounts/${accountName}/key-buckets/${bucketName}/consumers/${consumer.name}`,
      {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${environment.ZP_DEVELOPER_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          tags: {
            ...consumer.tags,
            plan: planName,
            lookups_limit: String(lookupsLimit),
          },
          metadata: {
            ...consumer.metadata,
            plan: planName,
          },
        }),
      },
    );

    if (!updateRes.ok) {
      context.log.error(
        `Failed to update consumer ${consumer.name}: ${await updateRes.text()}`,
      );
    } else {
      context.log.info(
        `Updated consumer ${consumer.name} to plan=${planName} limit=${lookupsLimit}`,
      );
    }
  }
}

export default async function (request: ZuploRequest, context: ZuploContext) {
  const payload = await request.text();
  const sigHeader = request.headers.get("stripe-signature");

  if (!sigHeader) {
    return new Response(JSON.stringify({ error: "Missing signature" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const valid = await verifyWebhookSignature(
    payload,
    sigHeader,
    environment.STRIPE_WEBHOOK_SECRET,
  );

  if (!valid) {
    context.log.error("Invalid webhook signature");
    return new Response(JSON.stringify({ error: "Invalid signature" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const event = JSON.parse(payload);
  context.log.info(`Stripe webhook: ${event.type}`);

  switch (event.type) {
    case "checkout.session.completed": {
      const session = event.data.object;

      if (!session.subscription) {
        context.log.warn("checkout.session.completed missing subscription");
        break;
      }

      // Fetch subscription to get the price and auth0_sub
      const subRes = await fetch(
        `https://api.stripe.com/v1/subscriptions/${session.subscription}`,
        {
          headers: {
            Authorization: `Bearer ${environment.STRIPE_SECRET_KEY}`,
          },
        },
      );
      const sub = await subRes.json();
      const priceId = sub.items?.data?.[0]?.price?.id;

      // Get auth0_sub from subscription metadata, session metadata, or customer metadata
      const auth0Sub = sub.metadata?.auth0_sub
        || session.metadata?.auth0_sub
        || session.subscription_data?.metadata?.auth0_sub;

      if (!auth0Sub) {
        context.log.warn("checkout.session.completed: no auth0_sub found in subscription or session metadata");
        break;
      }

      if (priceId && PRICE_TO_PLAN_MAP[priceId]) {
        const { name, limit } = PRICE_TO_PLAN_MAP[priceId];
        await updateConsumerPlan(auth0Sub, name, limit, context);
      }
      break;
    }

    case "customer.subscription.updated": {
      const subscription = event.data.object;
      const auth0Sub = subscription.metadata?.auth0_sub;
      const priceId = subscription.items?.data?.[0]?.price?.id;

      if (auth0Sub && priceId && PRICE_TO_PLAN_MAP[priceId]) {
        const { name, limit } = PRICE_TO_PLAN_MAP[priceId];
        await updateConsumerPlan(auth0Sub, name, limit, context);
      }
      break;
    }

    case "customer.subscription.deleted": {
      const subscription = event.data.object;
      const auth0Sub = subscription.metadata?.auth0_sub;

      if (auth0Sub) {
        await updateConsumerPlan(auth0Sub, "none", 0, context);
      }
      break;
    }
  }

  return new Response(JSON.stringify({ received: true }), {
    headers: { "Content-Type": "application/json" },
  });
}
