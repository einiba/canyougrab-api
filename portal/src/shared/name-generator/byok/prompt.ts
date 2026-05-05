/**
 * Shared prompt builder + parser for all BYOK providers. Kept in one place so
 * each adapter just routes the request — the prompt and JSON-extraction logic
 * stay consistent across providers.
 */

export function buildPrompt(args: {
  description: string;
  styles: string[];
  tldPref: string;
  count: number;
  anchors?: string[];
  exclude?: string[];
}): string {
  const styles = args.styles.length ? args.styles.join(", ") : "modern";
  const anchorBlock = args.anchors && args.anchors.length
    ? `\nSTYLISTIC ANCHORS (the user liked these — produce more in a similar vein, varying letters/sounds/composition but matching the feel): ${args.anchors.join(", ")}`
    : "";
  const excludeBlock = args.exclude && args.exclude.length
    ? `\nDO NOT REPEAT any of these (already shown to the user): ${args.exclude.slice(0, 60).join(", ")}`
    : "";
  return `You are a brand-naming expert. Generate ${args.count} candidate brand names for this business:

DESCRIPTION:
${args.description.slice(0, 1000)}

STYLES: ${styles}
PREFERRED EXTENSION TYPE: ${args.tldPref}${anchorBlock}${excludeBlock}

Rules:
- Each name is a single lowercase word or two words concatenated, alphanumeric only
- 4-18 characters
- Avoid generic words ("app", "platform", "startup", "company")
- Mix invented words, evocative metaphors, compound words, and direct descriptors
- Avoid offensive or trademark-likely names

Return ONLY a JSON array of strings, no commentary. Example: ["frondly", "treekit", "leafgraph"]`;
}

export function parseBases(text: string, count: number): string[] {
  const cleaned = text
    .replace(/^```(?:json)?\s*/m, "")
    .replace(/\s*```\s*$/m, "")
    .trim();

  let parsed: unknown;
  try {
    parsed = JSON.parse(cleaned);
  } catch {
    const match = cleaned.match(/\[[\s\S]*\]/);
    if (!match) throw new Error("Provider did not return a JSON array");
    parsed = JSON.parse(match[0]);
  }

  if (!Array.isArray(parsed)) {
    throw new Error("Provider did not return a JSON array");
  }

  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of parsed) {
    if (typeof item !== "string") continue;
    const cleaned = item.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 20);
    if (cleaned.length < 3 || cleaned.length > 20) continue;
    if (seen.has(cleaned)) continue;
    seen.add(cleaned);
    out.push(cleaned);
    if (out.length >= count) break;
  }
  return out;
}
