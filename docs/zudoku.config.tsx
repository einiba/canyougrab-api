import type { ZudokuConfig, ZudokuPlugin } from "zudoku";
import { PricingPage } from "./src/PricingPage.js";
import { UsageDashboard } from "./src/UsageDashboard.js";

const overrideCssPlugin: ZudokuPlugin = {
  getHead: () => <link rel="stylesheet" href="/overrides.css" />,
};

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
        light: "/logo-dark.svg",
        dark: "/logo-dark.svg",
      },
    },
  },
  metadata: {
    title: "CanYouGrab API",
    description: "Domain availability lookup API — fast, reliable, developer-friendly",
  },
  theme: {
    fonts: {
      sans: "Outfit",
      mono: "JetBrains Mono",
    },
    dark: {
      background: "#0a0b0d",
      foreground: "#e8eaed",
      card: "#12141a",
      cardForeground: "#e8eaed",
      popover: "#12141a",
      popoverForeground: "#e8eaed",
      primary: "#00d4aa",
      primaryForeground: "#0a0b0d",
      secondary: "#1a1d24",
      secondaryForeground: "#e8eaed",
      muted: "#1a1d24",
      mutedForeground: "#8b8f98",
      accent: "#1a1d24",
      accentForeground: "#e8eaed",
      destructive: "#ef4444",
      destructiveForeground: "#ffffff",
      border: "rgba(255, 255, 255, 0.06)",
      input: "rgba(255, 255, 255, 0.06)",
      ring: "#00d4aa",
    },
    customCss: `
      @font-face {
        font-family: 'Outfit';
        font-style: normal;
        font-weight: 300 700;
        font-display: swap;
        src: url(https://fonts.gstatic.com/s/outfit/v11/QGYyz_MVcBeNP4NjuGObqx1XmO1I4TC1O4a0Ew.woff2) format('woff2');
      }
      @font-face {
        font-family: 'JetBrains Mono';
        font-style: normal;
        font-weight: 400 500;
        font-display: swap;
        src: url(https://fonts.gstatic.com/s/jetbrainsmono/v18/tDbY2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKxTOlOTk6OThhvA.woff2) format('woff2');
      }
      /* Force dark mode */
      :root { color-scheme: dark; }
    `,
  },
  navigation: [
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
      to: "/settings/api-keys",
      label: "API Keys",
      display: "auth",
      icon: "key",
    },
    {
      type: "custom-page",
      path: "/pricing",
      label: "Plans & Pricing",
      element: <PricingPage />,
      icon: "tag",
    },
    {
      type: "link",
      to: "/docs",
      label: "API Reference",
    },
  ],
  redirects: [
    { from: "/", to: "/usage" },
    { from: "/docs", to: "/docs/~endpoints#bulk-domain-availability-check" },
  ],
  defaults: {
    apis: {
      showInfoPage: false,
    },
  },
  apis: [
    {
      type: "file",
      input: "../config/routes.oas.json",
      path: "docs",
    },
  ],
  authentication: {
    type: "auth0",
    domain: "dev-mqe5tavp6dr62e7u.us.auth0.com",
    clientId: "xeaTguUBeoeZg2PmetPVrnQmkud8Ikyq",
    audience: "https://api.canyougrab.it",
  },
  plugins: [overrideCssPlugin],
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
