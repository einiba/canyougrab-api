import { useNoIndex } from "@/hooks/useNoIndex";
import { NameGenerator } from "@/shared/name-generator";

/**
 * /interactive — describe-your-business → AI-generated names → live availability.
 *
 * The UI is the shared <NameGenerator /> imported from src/shared/, vendored
 * from canyougrab-site/src/shared/name-generator/ (see portal/scripts/README.md).
 * This page is just the chrome around it.
 */
export function InteractivePage() {
  useNoIndex();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Interactive Name Generator</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Describe your business — we'll suggest names that are actually available.
        </p>
      </div>

      <NameGenerator />
    </div>
  );
}
