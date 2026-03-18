import { Routes, Route, Navigate } from "react-router";
import { useAuth0 } from "@auth0/auth0-react";
import { AppLayout } from "@/components/AppLayout";
import { RequireAuth } from "@/components/RequireAuth";
import { UsageDashboard } from "@/pages/UsageDashboard";
import { PricingPage } from "@/pages/PricingPage";
import { CardSetupPage } from "@/pages/CardSetupPage";
import { ApiKeysPage } from "@/pages/ApiKeysPage";
import { DocsPage } from "@/pages/DocsPage";

export default function App() {
  const { isLoading } = useAuth0();

  // While Auth0 is processing an OAuth callback (?code=&state= in URL),
  // don't render routes — the <Navigate> on the index route would strip
  // the URL params before Auth0Provider can exchange the code for tokens.
  if (isLoading && window.location.search.includes("code=")) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
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
        <Route path="docs" element={<DocsPage />} />
      </Route>
    </Routes>
  );
}
