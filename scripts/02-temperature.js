import { config } from "dotenv";
config({ path: "../.env" });

import { createOpenAI } from "@ai-sdk/openai";
import { generateText } from "ai";

const ollama = createOpenAI({
  baseURL: process.env.OPENAI_BASE_URL,
  apiKey: process.env.OPENAI_API_KEY,
});

const PROMPT = "Write one sentence about the sea.";

async function run(temperature) {
  console.log(`\n=== temperature ${temperature} ===`);
  for (let i = 1; i <= 5; i++) {
    const result = await generateText({
      model: ollama.chat(process.env.MODEL),
      prompt: PROMPT,
      temperature,
    });
    console.log(`${i}. ${result.text.trim()}`);
  }
}

await run(0);
await run(1);
