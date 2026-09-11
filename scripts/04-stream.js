import { config } from "dotenv";
config({ path: "../.env", quiet: true });

import { createOpenAI } from "@ai-sdk/openai";
import { streamText } from "ai";

const ollama = createOpenAI({
  baseURL: process.env.OPENAI_BASE_URL,
  apiKey: process.env.OPENAI_API_KEY,
});

async function run(label, instructions, messages) {
  console.log(`\n=== ${label} ===`);

  const started = Date.now();
  let firstChunkAt = null;

  const result = streamText({
    model: ollama.chat(process.env.MODEL),
    instructions,
    messages,
    temperature: 0,
  });

  for await (const chunk of result.textStream) {
    if (firstChunkAt === null) firstChunkAt = Date.now();
    process.stdout.write(chunk);
  }

  const usage = await result.usage;
  const finishReason = await result.finishReason;

  console.log("\n---");
  console.log("time to first chunk:", firstChunkAt - started, "ms");
  console.log("total time:", Date.now() - started, "ms");
  console.log("usage:", usage);
  console.log("finish reason:", finishReason);
}

await run("long answer", "You are concise and factual.", [
  { role: "user", content: "Explain what the sea is, in three sentences." },
]);
