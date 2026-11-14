// Writes the default engines into OpenMausBot's config.json before startup (run by the omb-init service in compose.yaml).
// omb reads instances only when it starts, so this must run before omb comes up; omb-init has no network and runs as maus.
// Re-applied on every `docker compose up`: compose is the single source of these instances, so edits made to them in the UI are overwritten.
// Keys are not written to config.json: an instance only records apiKeyEnv, and the server reads the value from the environment (secrets.env) at run time.
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const file = join(process.env.HOME || "/data", ".openmausbot", "config.json");
const env = process.env;

// Note: as soon as config.json has any instances, it replaces OpenMausBot's built-in DEFAULT_FLEET (20+ cloud engines) entirely.
// Only local engines are listed here on purpose; add other engines from the UI, and this seed will not touch them.
const seeded = {
  // Built-in OpenAI-compatible engine: it has no config of its own, so url / key / model come from the OPENAI_COMPAT_* environment variables (the local LLM)
  openaiCompat: { driver: "openai-compat", displayName: "Local LLM" },
};
if (env.SEED_HERMES_URL) {
  seeded["hermes-agent"] = {
    driver: "openai-compat",
    displayName: "Hermes agent",
    access: "api",
    config: {
      url: env.SEED_HERMES_URL,
      apiKeyEnv: "HERMES_API_KEY",
      // Hermes' API server does not support tools / response_format (examples/hermes/note.md)
      tools: false,
      managedModels: [env.SEED_HERMES_MODEL || "hermes-agent"],
    },
  };
}

let cfg = {};
if (existsSync(file)) {
  try {
    cfg = JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    // A broken config.json is not overwritten; omb reports it itself, so the user's other settings are not wiped
    console.error(`[seed] ${file} is not valid JSON; leaving it untouched: ${error.message}`);
    process.exit(0);
  }
}

const instances = { ...(cfg.instances ?? {}) };
const before = JSON.stringify(instances);
Object.assign(instances, seeded);
if (JSON.stringify(instances) === before) {
  console.log(`[seed] engines already up to date: ${Object.keys(seeded).join(", ") || "(none)"}`);
  process.exit(0);
}

mkdirSync(dirname(file), { recursive: true, mode: 0o700 });
const tmp = `${file}.seed-${process.pid}`;
writeFileSync(tmp, `${JSON.stringify({ ...cfg, instances }, null, 2)}\n`, { mode: 0o600 });
renameSync(tmp, file);
console.log(`[seed] wrote ${Object.keys(seeded).join(", ")} to ${file}`);
