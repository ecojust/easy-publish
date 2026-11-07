// JSONL adapter for the bundled official Wenyan CLI render command.
// The CLI is invoked in memory: article text never becomes an OS argument.
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const THEMES = new Set(["default", "orangeheart", "rainbow", "lapis", "pie", "maize", "purple", "phycat"]);
const MAX_INPUT = 4 * 1024 * 1024;
const MAX_BODY = 500_000;
const MAX_OUTPUT = 16 * 1024 * 1024;
const ERROR = "文章渲染失败，请重试。";
const MAX_LINE = MAX_INPUT;
// The controlled CLI has no remote-resource, account or publishing commands.
globalThis.fetch = async () => { throw new Error("Remote resources are disabled"); };
const cliPath = pathToFileURL(path.join(path.dirname(fileURLToPath(import.meta.url)), "wenyan-cli.mjs")).href;
let cliPromise;

async function render(value) {
  if (!value || Array.isArray(value) || typeof value !== "object" ||
      Object.keys(value).some((key) => !["body", "theme"].includes(key)) ||
      typeof value.body !== "string" || value.body.length > MAX_BODY || !THEMES.has(value.theme)) throw new Error(ERROR);
  if (!value.body.trim()) return { html: "" };
  const { createProgram } = await (cliPromise ??= import(cliPath));
  const output = [];
  const previous = console.log;
  // Official render writes one completed HTML document to console.log. JSONL
  // is sequential; capture only this invocation, restoring output on failure.
  console.log = (text) => { if (typeof text !== "string" || Buffer.byteLength(text) > MAX_OUTPUT) throw new Error(ERROR); output.push(text); };
  try {
    const program = createProgram();
    program.configureOutput({ writeOut() {}, writeErr() {} }).exitOverride();
    await program.parseAsync(["render", "--theme", value.theme, "--", value.body], { from: "user" });
  } finally { console.log = previous; }
  if (output.length !== 1 || !output[0].includes('data-provider="WenYan"')) throw new Error(ERROR);
  const result = { html: output[0] };
  if (Buffer.byteLength(JSON.stringify(result), "utf8") + 1 > MAX_OUTPUT) throw new Error(ERROR);
  return result;
}

async function respond(line) {
  try {
    if (Buffer.byteLength(line, "utf8") > MAX_INPUT) throw new Error(ERROR);
    return await render(JSON.parse(line));
  } catch { return { error: ERROR }; }
}

async function main() {
  const streaming = process.argv.length === 3 && process.argv[2] === "--stdio";
  if (!streaming && process.argv.length !== 2) { process.stdout.write(JSON.stringify({ error: ERROR }) + "\n"); process.exitCode = 1; return; }
  let pending = "";
  let bytes = 0;
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    bytes += Buffer.byteLength(chunk, "utf8");
    pending += chunk;
    if ((!streaming && bytes > MAX_INPUT) || pending.length > MAX_LINE) throw new Error(ERROR);
    if (streaming) {
      let end;
      while ((end = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, end);
        pending = pending.slice(end + 1);
        process.stdout.write(JSON.stringify(await respond(line)) + "\n");
      }
    }
  }
  if (!streaming || pending) process.stdout.write(JSON.stringify(await respond(pending)) + "\n");
}

main().catch(() => { process.stdout.write(JSON.stringify({ error: ERROR }) + "\n"); process.exitCode = 1; });
