import { ApiReferenceReact } from "@scalar/api-reference-react";

export function DocsPage() {
  return (
    <div className="-mx-4 -mt-8">
      <ApiReferenceReact
        configuration={{
          url: "/openapi.json",
          darkMode: true,
          hideModels: true,
        }}
      />
    </div>
  );
}
