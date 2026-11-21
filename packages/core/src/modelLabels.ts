// Display names for LLM provider kinds and model ids. Settings and the usage panel show these; the API keeps the real ids.
const MODEL_ALIASES: Record<string, string> = {
  "claude-opus-5-5": "Model A Pro",
  "claude-sonnet-5-5": "Model A Fast",
  "claude-haiku-5-5": "Model A Lite",
};

/** The name to show for a model id; any other claude-* id is "Model A", other ids are shown as they are. */
export function modelLabel(id: string): string {
  if (MODEL_ALIASES[id]) return MODEL_ALIASES[id];
  return id.startsWith("claude-") ? "Model A" : id;
}

const KIND_LABELS: Record<string, string> = {
  gemini: "Gemini",
  vertex_gemini: "Vertex AI",
  gemini_cli: "Gemini CLI",
  antigravity_cli: "Antigravity CLI",
  claude_cli: "Model A (local CLI)",
};

/** The name to show for a provider kind. */
export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

/** A saved profile's name for display: a claude_cli profile named after its model (e.g. a name that starts with the vendor) shows the alias. */
export function profileLabel(p: { name: string; kind: string; model: string }): string {
  return p.kind === "claude_cli" && /claude/i.test(p.name) ? modelLabel(p.model) : p.name;
}
