import { ZuploContext, ZuploRequest } from "@zuplo/runtime";
import { findOrCreateCustomer, stripeRequest } from "./stripe-helpers";

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

  const customerId = await findOrCreateCustomer(sub, email);

  const session = await stripeRequest("POST", "billing_portal/sessions", {
    customer: customerId,
    return_url: `${DOCS_ORIGIN}/usage`,
  });

  if (session.error) {
    context.log.error(`Stripe portal error: ${JSON.stringify(session.error)}`);
    return new Response(
      JSON.stringify({ error: "Failed to create portal session" }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }

  return new Response(JSON.stringify({ url: session.url }), {
    headers: { "Content-Type": "application/json" },
  });
}
