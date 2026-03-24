import type { ZudokuConfig, ZudokuPlugin } from "zudoku";
import { PricingPage } from "./src/PricingPage.js";
import { UsageDashboard } from "./src/UsageDashboard.js";
import { API_BASE, TURNSTILE_SITE_KEY } from "./src/config.js";

const overrideCssPlugin: ZudokuPlugin = {
  getHead: () => (
    <>
      <link rel="stylesheet" href="/overrides.css" />
      <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" async defer />
      <script src="/chatwoot-init.js" defer />
    </>
  ),
};

/**
 * Execute Turnstile invisibly and return a token.
 * Falls back gracefully if Turnstile hasn't loaded yet.
 */
async function getTurnstileToken(): Promise<string> {
  const turnstile = (window as any).turnstile;
  if (!turnstile) return "";

  return new Promise<string>((resolve) => {
    // Create a hidden container for the invisible widget
    const container = document.createElement("div");
    container.style.display = "none";
    document.body.appendChild(container);

    turnstile.render(container, {
      sitekey: TURNSTILE_SITE_KEY,
      callback: (token: string) => {
        resolve(token);
        // Clean up
        try { turnstile.remove(container); } catch {}
        container.remove();
      },
      "error-callback": () => {
        resolve("");
        container.remove();
      },
      "expired-callback": () => {
        resolve("");
        container.remove();
      },
      size: "invisible",
    });
  });
}

/** Developer Portal Configuration */
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
    domain: "login.canyougrab.it",
    clientId: "Xz0TZK9Z2E9wN55FJVQYsMHsLougZzRm",
    audience: "https://api.canyougrab.it",
  },
  plugins: [overrideCssPlugin],
  apiKeys: {
    enabled: true,
    getConsumers: async (context: any) => {
      const req = new Request(`${API_BASE}/api/keys`);
      const signed = await context.signRequest(req);
      const res = await fetch(signed);
      if (!res.ok) return [];
      const keys = await res.json();
      return keys.filter((k: any) => k.active).map((k: any) => ({
        id: k.id,
        label: k.description || "API Key",
        description: k.description,
        createdOn: k.created_at,
        apiKeys: [{
          id: k.id,
          key: k.key_prefix + "...",
          createdOn: k.created_at,
        }],
      }));
    },
    createKey: async ({ apiKey, context }: any) => {
      const turnstileToken = await getTurnstileToken();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (turnstileToken) {
        headers["x-turnstile-token"] = turnstileToken;
      }
      const req = new Request(`${API_BASE}/api/keys`, {
        method: "POST",
        body: JSON.stringify({ description: apiKey.description || "API Key" }),
        headers,
      });
      const res = await fetch(await context.signRequest(req));
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Could not create API Key");
      }
    },
    rollKey: async (consumerId: string, context: any) => {
      const req = new Request(`${API_BASE}/api/keys/${consumerId}/rotate`, { method: "POST" });
      const res = await fetch(await context.signRequest(req));
      if (!res.ok) throw new Error("Could not rotate API Key");
    },
    deleteKey: async (consumerId: string, _keyId: string, context: any) => {
      const req = new Request(`${API_BASE}/api/keys/${consumerId}`, { method: "DELETE" });
      const res = await fetch(await context.signRequest(req));
      if (!res.ok) throw new Error("Could not revoke API Key");
    },
  },
};

export default config;
