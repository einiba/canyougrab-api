import { RedocStandalone } from "redoc";
import { API_BASE } from "@/config";

export function DocsPage() {
  return (
    <div className="-mx-4 -mt-8">
      <RedocStandalone
        specUrl={`${API_BASE}/api-reference/openapi.json`}
        options={{
          theme: {
            colors: {
              primary: { main: "#00d4aa" },
              text: { primary: "#e8eaed", secondary: "#8b8f98" },
              http: {
                get: "#61affe",
                post: "#49cc90",
                put: "#fca130",
                delete: "#f93e3e",
              },
            },
            typography: {
              fontFamily: '"Outfit", sans-serif',
              headings: { fontFamily: '"Outfit", sans-serif' },
              code: { fontFamily: '"JetBrains Mono", monospace' },
            },
            sidebar: {
              backgroundColor: "#0a0b0d",
              textColor: "#8b8f98",
              activeTextColor: "#00d4aa",
            },
            rightPanel: {
              backgroundColor: "#12141a",
            },
          },
          hideDownloadButton: false,
          expandResponses: "200",
          pathInMiddlePanel: true,
          nativeScrollbars: true,
        }}
      />
    </div>
  );
}
