/**
 * Plan parser — extracts structured steps from AI-generated plan output.
 * 
 * The AI outputs plans in markdown with numbered steps like:
 *   ## 1. Step Title
 *   Description text...
 *   ## 2. Another Step
 *   More text...
 */

export interface ParsedStep {
  title: string;
  description: string | null;
}

export function parsePlanOutput(content: string): ParsedStep[] {
  const steps: ParsedStep[] = [];
  const lines = content.split("\n");
  // Matches: "## 1. Title", "### 1. Title", "1. Title", "1) Title", "**1. Title**"
  const stepHeaderRegex = /^(?:#{1,4}\s*)?(?:\*\*)?(\d+)[.)]\s*(?:\*\*)?\s*(.+)/;

  let currentStep: ParsedStep | null = null;
  let descriptionLines: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();

    if (!trimmed) {
      if (currentStep) descriptionLines.push("");
      continue;
    }

    const match = trimmed.match(stepHeaderRegex);
    if (match) {
      if (currentStep) {
        currentStep.description = descriptionLines.join("\n").trim() || null;
        steps.push(currentStep);
      }
      let title = match[2].replace(/\*\*/g, "").replace(/`/g, "").trim();
      // Remove trailing dashes or colons
      title = title.replace(/\s*[-\u2013\u2014:]\s*$/, "").trim();
      currentStep = { title, description: null };
      descriptionLines = [];
      continue;
    }

    if (currentStep) {
      // Skip horizontal rules
      if (trimmed.match(/^---+$/)) continue;
      descriptionLines.push(trimmed);
    }
  }

  if (currentStep) {
    currentStep.description = descriptionLines.join("\n").trim() || null;
    steps.push(currentStep);
  }

  return steps.filter((s) => s.title.length > 0);
}