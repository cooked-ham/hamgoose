#!/usr/bin/env node
/**
 * @cooked-ham/hamgoose — npm launcher for the hamgoose Goose extension.
 *
 * hamgoose itself is a Python stdio MCP server (github.com/cooked-ham/hamgoose).
 * This package is a thin, dependency-free launcher with one job: make
 * `npx @cooked-ham/hamgoose` just work. It never contains mission logic.
 *
 *   npx -y @cooked-ham/hamgoose            run the MCP stdio server (what Goose spawns)
 *   npx @cooked-ham/hamgoose install       install the Python package (idempotent)
 *   npx @cooked-ham/hamgoose register      install + register with Goose
 *   npx @cooked-ham/hamgoose --version     print version
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import process from "node:process";
import path from "node:path";
import { createRequire } from "node:module";

const REPO = "https://github.com/cooked-ham/hamgoose.git";
const WIN = process.platform === "win32";
const require = createRequire(import.meta.url);
const VERSION = require("../package.json").version;

/** Run a command, capture output. cmd may be a "tool -flag" string (Windows). */
function run(cmd, args = [], inherit = false) {
  const r = spawnSync(cmd, args, {
    encoding: "utf8",
    shell: WIN,
    stdio: inherit ? "inherit" : "pipe",
  });
  return { code: r.status ?? 1, out: ((r.stdout || "") + (r.stderr || "")).trim() };
}

function which(name) {
  const r = WIN
    ? run("where", [name])
    : run("sh", ["-c", `command -v ${name}`]);
  if (r.code !== 0) return null;
  const line = r.out.split(/\r?\n/).find(Boolean);
  return line || null;
}

function pythonCandidates() {
  return WIN ? ["py -3", "python", "python3"] : ["python3", "python"];
}

/** Find a Python >= 3.11 command string, or null. */
function findPython() {
  for (const py of pythonCandidates()) {
    const ok = run(py, [
      "-c",
      "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)",
    ]);
    if (ok.code === 0) return py;
  }
  return null;
}

/** pip-install the Python package (idempotent). Returns success bool. */
function pipInstall(py) {
  console.log(`Installing hamgoose from ${REPO} …`);
  let r = run(py, ["-m", "pip", "install", "--user", REPO], true);
  if (r.code !== 0 && /externally managed/i.test(r.out)) {
    console.log("Retrying with --break-system-packages (PEP 668)…");
    r = run(py, ["-m", "pip", "install", "--user", "--break-system-packages", REPO], true);
  }
  if (r.code !== 0) {
    console.error("\nInstall failed. Manual fallback:\n  " +
      `    ${py} -m pip install ${REPO}\n` +
      "    (git must be on PATH for the git+ URL; see the repo README for a zip fallback)");
    return false;
  }
  return true;
}

/** Locate the installed `hamgoose` console script. Returns full path or null. */
function resolveServer(py) {
  const onPath = which("hamgoose");
  if (onPath) return onPath;
  if (py) {
    const base = run(py, ["-m", "site", "--user-base"]);
    if (base.code === 0) {
      const p = WIN
        ? path.join(base.out.trim(), "Scripts", "hamgoose.exe")
        : path.join(base.out.trim(), "bin", "hamgoose");
      if (existsSync(p)) return p;
    }
  }
  return null;
}

/** Ensure the Python package is installed. Returns server path or null. */
function ensureInstalled(quiet = false) {
  const py = findPython();
  let server = resolveServer(py);
  if (server) {
    if (!quiet) console.log(`hamgoose already installed (${server})`);
    return server;
  }
  if (!py) {
    console.error("Python 3.11+ not found. Install one (python.org or `uv`) and retry —\n" +
      "  or:  uv python install 3.12 && uv tool install git+" + REPO);
    return null;
  }
  if (!pipInstall(py)) return null;
  server = resolveServer(py);
  if (!quiet) console.log(server ? `Installed: ${server}` : "Installed (add your Python Scripts dir to PATH, or rerun).");
  return server;
}

function printHelp() {
  console.log(`hamgoose npm launcher v${VERSION}
Runs the Python hamgoose server (github.com/cooked-ham/hamgoose).

Usage:
  npx -y @cooked-ham/hamgoose            run the MCP stdio server (what Goose spawns)
  npx @cooked-ham/hamgoose install       install the Python package (idempotent)
  npx @cooked-ham/hamgoose register      install + register with Goose's config
  npx @cooked-ham/hamgoose --version     print version

Use with Goose: Add Extension (STDIO), command:
  npx -y @cooked-ham/hamgoose`);
}

const [, , ...args] = process.argv;

if (args.includes("--help") || args.includes("-h")) {
  printHelp();
  process.exit(0);
}
if (args.includes("--version")) {
  console.log(VERSION);
  process.exit(0);
}
if (args[0] === "install") {
  process.exit(ensureInstalled(false) ? 0 : 1);
}
if (args[0] === "register") {
  const py = findPython();
  const server = ensureInstalled(false) || resolveServer(py);
  if (!server) process.exit(1);
  console.log("Registering with Goose…");
  const r = spawnSync(server, ["register"], { stdio: "inherit", shell: WIN });
  process.exit(r.status ?? 1);
}

// Default: stdio server mode (Goose's extension command).
const server = ensureInstalled(true);
if (!server) process.exit(1);
const child = spawn(server, [], { stdio: "inherit", shell: WIN });
child.on("exit", (code) => process.exit(code ?? 0));
