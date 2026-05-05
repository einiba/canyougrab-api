import { Routes, Route, Navigate } from "react-router";
import { useAuth0 } from "@auth0/auth0-react";
import { AppLayout } from "@/components/AppLayout";
import { RequireAuth } from "@/components/RequireAuth";
import { UsageDashboard } from "@/pages/UsageDashboard";
import { PricingPage } from "@/pages/PricingPage";
import { CardSetupPage } from "@/pages/CardSetupPage";
import { ApiKeysPage } from "@/pages/ApiKeysPage";
import { DocsPage } from "@/pages/DocsPage";
import { InteractivePage } from "@/pages/InteractivePage";
import { SavedNamesPage } from "@/pages/SavedNamesPage";
import { SignupPage } from "@/pages/SignupPage";
import { TermsPage } from "@/pages/TermsPage";

export default function App() {
  const { isLoading, error: authError } = useAuth0();

  // While Auth0 is processing an OAuth callback (?code=&state= in URL),
  // don't render routes — the <Navigate> on the index route would strip
  // the URL params before Auth0Provider can exchange the code for tokens.
  if (isLoading && (window.location.search.includes("code=") || window.location.search.includes("error="))) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  // Handle Auth0 error responses (e.g. access_denied from account linking)
  if (authError) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="max-w-md p-6 rounded-lg border border-red-500/30 bg-red-500/10 text-center">
          <h2 className="text-xl font-semibold text-red-400 mb-2">Authentication Error</h2>
          <p className="text-sm text-gray-300 mb-4">
            {authError.message || "An error occurred during authentication."}
          </p>
          <a
            href="/"
            className="inline-block px-4 py-2 rounded bg-primary text-white text-sm hover:opacity-90"
          >
            Try Again
          </a>
        </div>
      </div>
    );
  }

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/usage" replace />} />
        <Route
          path="usage"
          element={
            <RequireAuth>
              <UsageDashboard />
            </RequireAuth>
          }
        />
        <Route
          path="api-keys"
          element={
            <RequireAuth>
              <ApiKeysPage />
            </RequireAuth>
          }
        />
        <Route path="pricing" element={<PricingPage />} />
        <Route
          path="card-setup"
          element={
            <RequireAuth>
              <CardSetupPage />
            </RequireAuth>
          }
        />
        <Route
          path="interactive"
          element={
            <RequireAuth>
              <InteractivePage />
            </RequireAuth>
          }
        />
        <Route
          path="saved-names"
          element={
            <RequireAuth>
              <SavedNamesPage />
            </RequireAuth>
          }
        />
        <Route path="docs" element={<DocsPage />} />
        <Route path="terms" element={<TermsPage />} />
        <Route path="signup" element={<SignupPage />} />
      </Route>
    </Routes>
  );
}
