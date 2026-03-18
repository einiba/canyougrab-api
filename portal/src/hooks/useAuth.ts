import { useAuth0 } from "@auth0/auth0-react";

export function useAuth() {
  const {
    isAuthenticated,
    isLoading,
    user,
    loginWithRedirect,
    logout: auth0Logout,
  } = useAuth0();

  return {
    isAuthenticated,
    isPending: isLoading,
    profile: user
      ? {
          sub: user.sub ?? "",
          email: user.email ?? "",
          name: user.name ?? "",
          pictureUrl: user.picture ?? "",
        }
      : null,
    login: () => loginWithRedirect(),
    logout: () =>
      auth0Logout({ logoutParams: { returnTo: window.location.origin } }),
  };
}
