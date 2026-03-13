import { ZuploContext, ZuploRequest, environment } from "@zuplo/runtime";

const accountName = environment.ZP_ACCOUNT_NAME;
const bucketName = environment.ZP_API_KEY_SERVICE_BUCKET_NAME;

export default async function (request: ZuploRequest, context: ZuploContext) {
  const sub = request.user?.sub;
  const body = await request.json();

  const response = await fetch(
    `https://dev.zuplo.com/v1/accounts/${accountName}/key-buckets/${bucketName}/consumers?with-api-key=true`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${environment.ZP_DEVELOPER_API_KEY}`,
      },
      body: JSON.stringify({
        name: crypto.randomUUID(),
        managers: [
          { email: body.email ?? "nobody@example.com", sub: sub },
        ],
        description: body.description ?? "API Key",
        tags: {
          sub: sub,
          email: body.email,
        },
        metadata: {},
      }),
    },
  );

  if (!response.ok) {
    const errorText = await response.text();
    context.log.error(`Failed to create consumer: ${errorText}`);
    return new Response(
      JSON.stringify({ error: "Failed to create API key" }),
      { status: response.status, headers: { "Content-Type": "application/json" } },
    );
  }

  return response.json();
}
