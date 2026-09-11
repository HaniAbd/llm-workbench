import { config } from "dotenv";
config({ path: "../.env", quiet: true });

import { createOpenAI } from "@ai-sdk/openai";
import { generateText } from "ai";

const ollama = createOpenAI({
  baseURL: process.env.OPENAI_BASE_URL,
  apiKey: process.env.OPENAI_API_KEY,
});

async function run(label, instructions, messages) {
  const result = await generateText({
    model: ollama.chat(process.env.MODEL),
    instructions,
    messages,
    temperature: 0,
  });
  console.log(`\n=== ${label} ===`);
  console.log(result.text.trim());
  console.log("input tokens:", result.usage.inputTokens);
}

await run("pirate", "You are a pirate. Answer in one sentence.", [
  { role: "user", content: "What is the capital of France?" },
]);

await run("database", "You reply with a single word, no punctuation.", [
  { role: "user", content: "What is the capital of France?" },
]);

await run("with history", "You reply with a single word, no punctuation.", [
  { role: "user", content: "What is the capital of France?" },
  { role: "assistant", content: "Paris" },
  { role: "user", content: "And of Spain?" },
]);
