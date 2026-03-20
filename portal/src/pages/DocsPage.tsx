import { useEffect, useRef } from "react";

const ZUDOKU_CSS_ID = "zudoku-css";
const ZUDOKU_JS_ID = "zudoku-js";

export function DocsPage() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!document.getElementById(ZUDOKU_CSS_ID)) {
      const link = document.createElement("link");
      link.id = ZUDOKU_CSS_ID;
      link.rel = "stylesheet";
      link.href = "https://cdn.zudoku.dev/latest/zudoku.css";
      document.head.appendChild(link);
    }

    if (!document.getElementById(ZUDOKU_JS_ID)) {
      const script = document.createElement("script");
      script.id = ZUDOKU_JS_ID;
      script.type = "module";
      script.src = "https://cdn.zudoku.dev/latest/main.js";
      document.body.appendChild(script);
    }
  }, []);

  return (
    <div className="-mx-4 -mt-8" style={{ minHeight: "calc(100vh - 3.5rem)" }}>
      <div ref={containerRef} data-api-url="/openapi.json" />
    </div>
  );
}
