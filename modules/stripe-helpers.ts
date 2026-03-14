import { environment } from "@zuplo/runtime";

const STRIPE_API = "https://api.stripe.com/v1";

export const PLAN_PRICE_MAP: Record<
  string,
  { priceId: string; limit: number }
> = {
  starter: { priceId: "price_1TAggjH8ksFkvmqRKVBO4YhN", limit: 100 },
  basic: { priceId: "price_1TAggjH8ksFkvmqRNEE6UHx3", limit: 10_000 },
  pro: { priceId: "price_1TAggkH8ksFkvmqRUx9kVWf9", limit: 50_000 },
  business: { priceId: "price_1TAggkH8ksFkvmqRn7c63MZE", limit: 300_000 },
};

export const PRICE_TO_PLAN_MAP: Record<
  string,
  { name: string; limit: number }
> = Object.fromEntries(
  Object.entries(PLAN_PRICE_MAP).map(([name, { priceId, limit }]) => [
    priceId,
    { name, limit },
  ]),
);

export const PLAN_LIMITS: Record<string, number> = Object.fromEntries(
  Object.entries(PLAN_PRICE_MAP).map(([name, { limit }]) => [name, limit]),
);

function encodeBody(
  obj: Record<string, any>,
  prefix = "",
): URLSearchParams {
  const params = new URLSearchParams();
  function flatten(o: any, p: string) {
    if (o === null || o === undefined) return;
    if (typeof o === "object" && !Array.isArray(o)) {
      for (const [k, v] of Object.entries(o)) {
        flatten(v, p ? `${p}[${k}]` : k);
      }
    } else if (Array.isArray(o)) {
      for (let i = 0; i < o.length; i++) {
        flatten(o[i], `${p}[${i}]`);
      }
    } else {
      params.append(p, String(o));
    }
  }
  flatten(obj, prefix);
  return params;
}

export async function stripeRequest(
  method: string,
  path: string,
  body?: Record<string, any>,
): Promise<any> {
  const url = `${STRIPE_API}/${path}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${environment.STRIPE_SECRET_KEY}`,
  };

  const opts: RequestInit = { method, headers };

  if (body && (method === "POST" || method === "PATCH")) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    opts.body = encodeBody(body).toString();
  }

  const res = await fetch(url, opts);
  return res.json();
}

export async function findOrCreateCustomer(
  auth0Sub: string,
  email?: string,
): Promise<string> {
  // Search for existing customer by auth0_sub metadata
  const search = await stripeRequest(
    "GET",
    `customers/search?query=${encodeURIComponent(`metadata['auth0_sub']:'${auth0Sub}'`)}`,
  );

  if (search.data?.length > 0) {
    return search.data[0].id;
  }

  // Create new customer
  const customer = await stripeRequest("POST", "customers", {
    email,
    metadata: { auth0_sub: auth0Sub },
  });

  return customer.id;
}

export async function getActiveSubscription(
  customerId: string,
): Promise<any | null> {
  const subs = await stripeRequest(
    "GET",
    `subscriptions?customer=${customerId}&status=active&limit=1`,
  );

  return subs.data?.[0] || null;
}

export async function verifyWebhookSignature(
  payload: string,
  sigHeader: string,
  secret: string,
): Promise<boolean> {
  const parts = sigHeader.split(",").reduce(
    (acc, part) => {
      const [key, value] = part.split("=");
      acc[key.trim()] = value;
      return acc;
    },
    {} as Record<string, string>,
  );

  const timestamp = parts["t"];
  const signature = parts["v1"];

  if (!timestamp || !signature) return false;

  // Check timestamp tolerance (5 minutes)
  const age = Math.floor(Date.now() / 1000) - parseInt(timestamp);
  if (Math.abs(age) > 300) return false;

  // Compute expected signature
  const signedPayload = `${timestamp}.${payload}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(signedPayload),
  );

  const expected = Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  // Constant-time comparison
  if (expected.length !== signature.length) return false;
  let result = 0;
  for (let i = 0; i < expected.length; i++) {
    result |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return result === 0;
}
