import type { RuntimeExtensions } from "@zuplo/runtime";

export function runtimeInit(runtime: RuntimeExtensions) {
  // TODO: Enable Stripe Monetization Plugin after gaining beta access.
  // Uncomment the following once @zuplo/runtime exports StripeMonetizationPlugin:
  //
  // import { StripeMonetizationPlugin } from "@zuplo/runtime";
  // StripeMonetizationPlugin.register(runtime, {
  //   webhooks: {
  //     signingSecret: "$env(STRIPE_WEBHOOK_SECRET)",
  //   },
  //   stripeSecretKey: "$env(STRIPE_SECRET_KEY)",
  // });
}
