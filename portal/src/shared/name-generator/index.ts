/**
 * Public API for the shared name-generator UI.
 *
 * Source of truth: this directory in the canyougrab-site repo.
 * Mirrored to canyougrab-api/portal via portal/scripts/sync-shared.mjs.
 *
 * The shared code expects two app-provided modules at standard path aliases:
 *   - `@/config` exporting:   API_BASE_URL, PORTAL_URL, namecheapRegisterUrl, porkbunRegisterUrl
 *   - `@/lib/logger` exporting:  `logger` with .info/.warn/.error methods
 *
 * Both apps must satisfy this contract. The marketing site exports them
 * directly; the portal aliases its existing keys.
 */

export { default as NameGenerator } from "./NameGenerator";
export { default as NameCard } from "./NameCard";
export { default as FavoritesTray } from "./FavoritesTray";
export { default as ByokIndicator } from "./ByokIndicator";
export { default as ByokSettings } from "./ByokSettings";

export {
  generateNames,
  ByokLimitError,
  TrialExhaustedError,
  type GenerateNamesRequest,
  type GenerateNamesResponse,
  type GeneratedName,
  type NameStyle,
  type TldPreference,
  type GenerationMode,
} from "./nameGen";

export {
  readByokKey,
  writeByokKey,
  clearByokKey,
  hasByokKey,
  subscribe as subscribeByok,
} from "./byok/storage";

export {
  PROVIDERS,
  type ProviderId,
  type ProviderMeta,
  type ByokKey,
} from "./byok/types";

export { getVisitorId, getVisitorHeaders, claimAnonLists, withVisitorId } from "./visitor";
