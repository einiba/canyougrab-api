import { ApiReferenceReact } from "@scalar/api-reference-react";
import { API_BASE } from "@/config";

export function DocsPage() {
  return (
    <div className="-mx-4 -mt-8">
      <ApiReferenceReact
        configuration={{
          url: `${API_BASE}/api-reference/openapi.json`,
          darkMode: true,
          hideModels: true,
        }}
      />
    </div>
  );
}
