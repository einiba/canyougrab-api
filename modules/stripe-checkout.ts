import { ZuploContext, ZuploRequest } from "@zuplo/runtime";
import { PLAN_PRICE_MAP, findOrCreateCustomer, stripeRequest } from "./stripe-helpers";

const DOCS_ORIGIN = "https://portal.canyougrab.it";

export default async function (request: ZuploRequest, context: ZuploContext) {
  const sub = request.user?.sub;
  const email = (request.user as any)?.data?.email;

  if (!sub) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  let body: { plan?: string };
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const plan = body.plan?.toLowerCase();
  if (!plan || !PLAN_PRICE_MAP[plan]) {
    return new Response(
      JSON.stringify({
        error: "Invalid plan",
        valid: Object.keys(PLAN_PRICE_MAP),
      }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const customerId = await findOrCreateCustomer(sub, email);

  const session = await stripeRequest("POST", "checkout/sessions", {
    customer: customerId,
    mode: "subscription",
    line_items: [{ price: PLAN_PRICE_MAP[plan].priceId, quantity: 1 }],
    success_url: `${DOCS_ORIGIN}/usage?checkout=success`,
    cancel_url: `${DOCS_ORIGIN}/pricing?checkout=cancel`,
    subscription_data: {
      metadata: { auth0_sub: sub },
    },
    allow_promotion_codes: "true",
  });

  if (session.error) {
    context.log.error(`Stripe checkout error: ${JSON.stringify(session.error)}`);
    return new Response(
      JSON.stringify({ error: "Failed to create checkout session" }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }

  return new Response(JSON.stringify({ url: session.url }), {
    headers: { "Content-Type": "application/json" },
  });
}
