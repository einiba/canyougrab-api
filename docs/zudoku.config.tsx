import type { ZudokuConfig } from "zudoku";
import { UsageDashboard } from "./src/UsageDashboard.js";

/**
 * Developer Portal Configuration
 * For more information, see:
 * https://zuplo.com/docs/dev-portal/zudoku/configuration/overview
 */
const config: ZudokuConfig = {
  site: {
    title: "CanYouGrab API",
    logo: {
      src: {
        light: "https://placehold.co/200x40/white/333?text=CanYouGrab",
        dark: "https://placehold.co/200x40/1a1a2e/white?text=CanYouGrab",
      },
    },
  },
  metadata: {
    title: "CanYouGrab API",
    description: "Domain availability lookup API — fast, reliable, developer-friendly",
  },
  navigation: [
    {
      type: "link",
      to: "/settings/api-keys",
      label: "API Keys",
      display: "auth",
      icon: "key",
    },
    {
      type: "custom-page",
      path: "/usage",
      label: "Usage & Billing",
      element: <UsageDashboard />,
      display: "auth",
      icon: "bar-chart",
    },
    {
      type: "link",
      to: "/api",
      label: "API Reference",
    },
  ],
  redirects: [{ from: "/", to: "/settings/api-keys" }],
  apis: [
    {
      type: "file",
      input: "../config/routes.oas.json",
      path: "api",
    },
  ],
  authentication: {
    type: "auth0",
    domain: "dev-mqe5tavp6dr62e7u.us.auth0.com",
    clientId: "xeaTguUBeoeZg2PmetPVrnQmkud8Ikyq",
    audience: "https://api.canyougrab.it",
  },
  apiKeys: {
    enabled: true,
    createKey: async ({ apiKey, context, auth }: any) => {
      const serverUrl =
        (typeof process !== "undefined" &&
          process.env?.ZUPLO_PUBLIC_SERVER_URL) ||
        (import.meta as any).env?.ZUPLO_SERVER_URL;
      const createApiKeyRequest = new Request(
        serverUrl + "/v1/developer/api-key",
        {
          method: "POST",
          body: JSON.stringify({
            ...apiKey,
            email: auth.profile?.email,
            metadata: {
              userId: auth.profile?.sub,
              name: auth.profile?.name,
            },
          }),
          headers: {
            "Content-Type": "application/json",
          },
        },
      );

      const response = await fetch(
        await context.signRequest(createApiKeyRequest),
      );

      if (!response.ok) {
        throw new Error("Could not create API Key");
      }

      return true;
    },
  },
};

export default config;
