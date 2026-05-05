import { useCallback } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import { useNoIndex } from "@/hooks/useNoIndex";
import { NameGenerator } from "@/shared/name-generator";
import { AUTH0_AUDIENCE } from "@/config";

/**
 * /interactive — describe-your-business → AI-generated names → live availability,
 * OR paste a list of domains and just check availability.
 *
 * The UI is the shared <NameGenerator /> imported from src/shared/, vendored
 * from canyougrab-site/src/shared/name-generator/ (see portal/scripts/README.md).
 * Passing `getAccessToken` switches Check mode to the portal-authenticated
 * /api/portal/check/bulk endpoint instead of the anon /api/names/check path.
 */
export function InteractivePage() {
  useNoIndex();
  const { getAccessTokenSilently } = useAuth0();

  const getAccessToken = useCallback(
    () =>
      getAccessTokenSilently({
        authorizationParams: { audience: AUTH0_AUDIENCE },
      }),
    [getAccessTokenSilently],
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Interactive Name Generator</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Describe your business — or paste a list — and we'll check availability live.
        </p>
      </div>

      <NameGenerator getAccessToken={getAccessToken} />
    </div>
  );
}
