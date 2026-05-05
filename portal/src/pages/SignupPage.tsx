import { useEffect } from "react";
import { Navigate } from "react-router";
import { useAuth0 } from "@auth0/auth0-react";

/**
 * /signup — fires Auth0 universal login with screen_hint=signup so users land
 * on the create-account view rather than the sign-in view. After auth, the
 * user lands on /usage (default post-login destination).
 *
 * If the user is already authenticated, redirect straight to /usage instead
 * of pestering them with the Auth0 round trip.
 */
export function SignupPage() {
  const { isAuthenticated, isLoading, loginWithRedirect } = useAuth0();

  useEffect(() => {
    if (isLoading) return;
    if (isAuthenticated) return;
    void loginWithRedirect({
      authorizationParams: { screen_hint: "signup" },
    });
  }, [isAuthenticated, isLoading, loginWithRedirect]);

  if (isAuthenticated) {
    return <Navigate to="/usage" replace />;
  }

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="text-center">
        <div className="animate-spin mx-auto rounded-full h-8 w-8 border-2 border-primary border-t-transparent mb-4" />
        <p className="text-sm text-muted-foreground">Taking you to signup…</p>
      </div>
    </div>
  );
}
