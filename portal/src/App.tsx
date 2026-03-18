import { Routes, Route, Navigate } from "react-router";
import { AppLayout } from "@/components/AppLayout";
import { RequireAuth } from "@/components/RequireAuth";
import { UsageDashboard } from "@/pages/UsageDashboard";
import { PricingPage } from "@/pages/PricingPage";
import { CardSetupPage } from "@/pages/CardSetupPage";
import { ApiKeysPage } from "@/pages/ApiKeysPage";
import { DocsPage } from "@/pages/DocsPage";

export default function App() {
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
