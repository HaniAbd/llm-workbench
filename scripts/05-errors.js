import { config } from "dotenv";
config({ path: "../.env", quiet: true });

import { createOpenAI } from "@ai-sdk/openai";
import { generateText } from "ai";

const ollama = createOpenAI({
  baseURL: process.env.OPENAI_BASE_URL,
  apiKey: process.env.OPENAI_API_KEY,
});

// a provider pointed at a port with nothing on it
const broken = createOpenAI({
  baseURL: "http://localhost:9999/v1",
  apiKey: "nope",
});

// --- 1. truncation ---------------------------------------------------
async function truncation() {
  console.log("\n=== 1. truncation ===");

  const result = await generateText({
    model: ollama.chat(process.env.MODEL),
    prompt: "Explain the history of the Mediterranean sea in detail.",
    temperature: 0,
    maxOutputTokens: 20,
  });

  console.log("text:", result.text.trim());
  console.log("finish reason:", result.finishReason);
  console.log("output tokens:", result.usage.outputTokens);

  if (result.finishReason === "length") {
    console.log(">> answer was cut off, do not parse this");
  }
}

// --- 2. timeout ------------------------------------------------------
async function timeout() {
  console.log("\n=== 2. timeout ===");

  try {
    await generateText({
      model: ollama.chat(process.env.MODEL),
      prompt: "Write a long essay about the sea.",
      temperature: 0,
      abortSignal: AbortSignal.timeout(300),
    });
    console.log("finished before the timeout, lower the value");
  } catch (err) {
    console.log("caught:", err.name, "-", err.message);
  }
}

// --- 3. retry with backoff -------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function withRetry(fn, attempts = 3, baseDelay = 500) {
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt === attempts) throw err;
      const delay = baseDelay * 2 ** (attempt - 1);
      console.log(`attempt ${attempt} failed, waiting ${delay}ms`);
      await sleep(delay);
    }
  }
}

async function retry() {
  console.log("\n=== 3. retry ===");

  const started = Date.now();

  try {
    await withRetry(() =>
      generateText({
        model: broken.chat("whatever"),
        prompt: "hello",
        maxRetries: 0, // disable the SDK's own retries
      }),
    );
  } catch (err) {
    console.log("gave up after", Date.now() - started, "ms");
    console.log("final error:", err.name);
  }
}

await truncation();
await timeout();
await retry();
