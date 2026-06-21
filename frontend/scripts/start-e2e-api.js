const { spawn, spawnSync } = require("child_process");
const path = require("path");

const apiRoot = path.resolve(__dirname, "..", "..", "api");
const env = {
  ...process.env,
  DATABASE_URL:
    process.env.E2E_DATABASE_URL || "sqlite+pysqlite:///./e2e-playwright.db",
};

const migrate = spawnSync("python", ["-m", "alembic", "upgrade", "head"], {
  cwd: apiRoot,
  env,
  stdio: "inherit",
  shell: true,
});

if (migrate.status !== 0) {
  process.exit(migrate.status ?? 1);
}

const server = spawn(
  "python",
  ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
  {
    cwd: apiRoot,
    env,
    stdio: "inherit",
    shell: true,
  }
);

function shutdown() {
  if (!server.killed) {
    server.kill();
  }
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
server.on("exit", (code) => process.exit(code ?? 0));
