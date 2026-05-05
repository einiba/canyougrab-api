const hostname =
  typeof window !== "undefined" ? window.location.hostname : "";
const isDev =
  hostname.includes("dev") ||
  hostname === "localhost" ||
  hostname === "127.0.0.1";

export const API_BASE = isDev
  ? "https://dev-api.canyougrab.it"
  : "https://api.canyougrab.it";

export const TURNSTILE_SITE_KEY = "0x4AAAAAAACsQV7KWrZtGtFSP";

export const STRIPE_PUBLISHABLE_KEY = isDev
  ? "pk_test_51TAgYEH8ksFkvmqRdYzxRtWkDj3LSUBfmCCZaptWk51v3PmfA1KEVP3pf9uNdFcLMe1I4XsTjsVbkZY8TT3kdbja00mtrdGXuQ"
  : "pk_live_51TAgY4HWwGSUcGDUBDXCumqJ2b9arnP0ECAU9SsrLPpsutlLS9Z1CaUSs9qrkrG9kiVAlfaJ7TX5AEEqvG5O2BZE00y6PFDcwh";

export const AUTH0_DOMAIN = "login.canyougrab.it";
export const AUTH0_CLIENT_ID = "Xz0TZK9Z2E9wN55FJVQYsMHsLougZzRm";
export const AUTH0_AUDIENCE = "https://api.canyougrab.it";

// ─── Contract for shared/name-generator (synced from canyougrab-site) ─────
// The shared code imports these symbols from `@/config`. Re-export portal's
// values under the names the shared code expects.

export const API_BASE_URL = API_BASE;
export const PORTAL_URL = isDev ? "https://dev-portal.canyougrab.it" : "https://portal.canyougrab.it";

const NAMECHEAP_AFFILIATE_ID = (import.meta.env.VITE_NAMECHEAP_AFFILIATE_ID as string | undefined) ?? "";
const PORKBUN_AFFILIATE_ID = (import.meta.env.VITE_PORKBUN_AFFILIATE_ID as string | undefined) ?? "";

export function namecheapRegisterUrl(domain: string): string {
  const base = `https://www.namecheap.com/domains/registration/results/?domain=${encodeURIComponent(domain)}`;
  return NAMECHEAP_AFFILIATE_ID
    ? `${base}&aff=${encodeURIComponent(NAMECHEAP_AFFILIATE_ID)}`
    : base;
}

export function porkbunRegisterUrl(domain: string): string {
  const base = `https://porkbun.com/checkout/search?q=${encodeURIComponent(domain)}`;
  return PORKBUN_AFFILIATE_ID
    ? `${base}&ref=${encodeURIComponent(PORKBUN_AFFILIATE_ID)}`
    : base;
}
