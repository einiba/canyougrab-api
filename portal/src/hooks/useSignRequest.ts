import { useAuth0 } from "@auth0/auth0-react";
import { useCallback } from "react";
import { AUTH0_AUDIENCE } from "@/config";

export function useSignRequest() {
  const { getAccessTokenSilently } = useAuth0();

  const signRequest = useCallback(
    async (request: Request): Promise<Request> => {
      const token = await getAccessTokenSilently({
        authorizationParams: { audience: AUTH0_AUDIENCE },
      });
      const signed = new Request(request, {
        headers: new Headers(request.headers),
      });
      signed.headers.set("Authorization", `Bearer ${token}`);
      return signed;
    },
    [getAccessTokenSilently],
  );

  return { signRequest };
}
