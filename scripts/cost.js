// Prices per 1M tokens. Local = 0.
// Replace with real provider numbers when you switch.
const PRICING = {
  "llama3.2": { input: 0, output: 0 },
  "gpt-4o-mini": { input: 0.15, output: 0.6 },
};

export function computeCost(model, usage) {
  const p = PRICING[model] ?? { input: 0, output: 0 };
  const input = (usage.inputTokens / 1_000_000) * p.input;
  const output = (usage.outputTokens / 1_000_000) * p.output;
  return { input, output, total: input + output };
}
