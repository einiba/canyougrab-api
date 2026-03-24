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

export const AUTH0_DOMAIN = isDev
  ? "dev-4yl51qvnwwd6g7xe.us.auth0.com"
  : "login.canyougrab.it";
export const AUTH0_CLIENT_ID = isDev
  ? "Xz0TZK9Z2E9wN55FJVQYsMHsLougZzRm"
  : "xeaTguUBeoeZg2PmetPVrnQmkud8Ikyq";
export const AUTH0_AUDIENCE = "https://api.canyougrab.it";
