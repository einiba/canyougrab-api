import { ZuploContext, ZuploRequest } from "@zuplo/runtime";

/**
 * Adds the authenticated consumer's subject as an X-Consumer header
 * so the upstream backend can identify the caller for usage tracking.
 */
export default async function (
  request: ZuploRequest,
  context: ZuploContext
) {
  if (request.user?.sub) {
    request.headers.set("x-consumer", request.user.sub);
  }
  const plan = (request.user as any)?.data?.plan ?? "";
  if (plan) {
    request.headers.set("x-consumer-plan", plan.toLowerCase());
  }
  return request;
}
