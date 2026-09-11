import { createOpenAI } from "@ai-sdk/openai";
import { generateText } from "ai";
import { config } from "dotenv";
import { computeCost } from "./cost.js";
config({ path: "../.env" });

const ollama = createOpenAI({
  baseURL: process.env.OPENAI_BASE_URL,
  apiKey: process.env.OPENAI_API_KEY,
});

const result = await generateText({
  model: ollama.chat(process.env.MODEL),
  prompt: "What is the capital of France? Answer in one word.",
});

console.log("--- text ---");
console.log(result.text);

console.log("--- usage ---");
console.log(result.usage);

console.log("--- finish reason ---");
console.log(result.finishReason);

console.log("--- cost ---");
console.log("local  ", computeCost("llama3.2", result.usage));
console.log("if paid", computeCost("gpt-4o-mini", result.usage));
