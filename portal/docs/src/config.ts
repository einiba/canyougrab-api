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

export const CHATWOOT_BASE_URL = "https://chatwoot.canyougrab.it";

export const CHATWOOT_WEBSITE_TOKEN = isDev
  ? "99as6UKZNBJkBknq76TZoZcx"
  : "hXtVKFiB5VSRJJFQMnLakXo3";
