import { useEffect } from "react";

export function DocsPage() {
  useEffect(() => {
    window.location.replace("/docs/");
  }, []);

  return null;
}
