#!/usr/bin/env node
/**
 * @cooked-ham/hamgoose — npm launcher for the hamgoose Goose extension.
 *
 * hamgoose itself is a Python stdio MCP server (github.com/cooked-ham/hamgoose).
 * This package is a thin, dependency-free launcher with one job: make
 * `npx @cooked-ham/hamgoose` (or a globally installed `hamgoose`) just work.
 * It never contains mission logic.
 *
 *   hamgoose                    run the MCP stdio server (what Goose spawns)
 *   hamgoose install            install the Python package (idempotent)
 *   hamgoose register           install + register with Goose
 *   hamgoose unregister         remove from Goose's config
 *   hamgoose help               show help
 *   hamgoose --version          print version
 *
 * Design notes (fix for 0.1.0 "infinite wall" bug):
 * - The npm bin is named `hamgoose`, the SAME as the Python console script.
 *   Resolving "the server" via `where hamgoose` therefore finds THIS launcher's
 *   own global shim first and spawns itself forever. So the preferred
 *   resolution is `python -m hamgoose` (verified by importing the module with
 *   a known interpreter — PATH is never consulted), and any PATH hit that
 *   resolves back to this package is rejected (isOurOwn()).
 * - On Windows we spawn a single quoted command line with an EMPTY args array.
 *   shell:true + args is deprecated (node DEP0190) and unsafe.
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import process from "node:process";
import path from "node:path";
import { createRequire } from "node:module";

const REPO = "https://github.com/cooked-ham/hamgoose.git";
const WIN = process.platform === "win32";
const require = createRequire(import.meta.url);
const VERSION = require("../package.json").version;
// This launcher's own package dir (…/node_modules/@cooked-ham/hamgoose, or the
// repo's npm/ dir when run from a checkout). Anything resolving inside here is
// us, not the Python server.
const PKG_ROOT = path.dirname(require.resolve("../package.json"));

/** Quote one token for cmd.exe. */
function q(token) {
  const t = String(token);
  return /[ \t"]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
}

/**
 * Run a command, capture output.
 * Windows: build ONE quoted command line and pass no args (shell:true + args
 * triggers DEP0190 and is unsafe). POSIX: plain exec with args, no shell.
 */
function run(cmd, args = [], inherit = false) {
  const stdio = inherit ? "inherit" : "pipe";
  const r = WIN
    ? spawnSync([cmd, ...args].map(q).join(" "), [], { encoding: "utf8", shell: true, stdio })
    : spawnSync(cmd, args, { encoding: "utf8", stdio });
  return { code: r.status ?? 1, out: ((r.stdout || "") + (r.stderr || "")).trim() };
}

/** Spawn the server the same safe way. sync=true returns a SpawnSync result. */
function launch(cmdArgs, extra = [], sync = false, env = process.env) {
  const all = [cmdArgs[0], ...cmdArgs[1], ...extra];
  if (WIN) {
    const line = all.map(q).join(" ");
    return sync
      ? spawnSync(line, [], { stdio: "inherit", shell: true, env })
      : spawn(line, [], { stdio: "inherit", shell: true, env });
  }
  return sync
    ? spawnSync(all[0], all.slice(1), { stdio: "inherit", env })
    : spawn(all[0], all.slice(1), { stdio: "inherit", env });
}

/** True if p points at THIS launcher (npm shim / our mjs), not the Python server. */
function isOurOwn(p) {
  try {
    let rp = path.resolve(p);
    try { rp = realpathSync(rp); } catch { /* keep resolved path */ }
    if (rp.startsWith(PKG_ROOT + path.sep)) return true;            // inside this package
    const base = path.basename(rp).toLowerCase();
    if (base === "hamgoose.cmd" || base === "hamgoose.ps1") return true; // npm shims
    if (!path.extname(base)) {                                      // bare `hamgoose` script
      try {
        const txt = readFileSync(rp, "utf8");
        if (txt.length < 8192 && /hamgoose\.mjs|@cooked-ham\/hamgoose/.test(txt)) return true;
      } catch { /* unreadable → not ours */ }
    }
  } catch { /* never treat a failure as "not ours" in a way that matters */ }
  return false;
}

function which(name) {
  const r = WIN
    ? run("where", [name])
    : run("sh", ["-c", `command -v ${name}`]);
  if (r.code !== 0) return null;
  // Skip hits that are this launcher's own bin — the npm global `hamgoose`
  // shim shadows the Python `hamgoose` console script (same name!).
  const line = r.out.split(/\r?\n/).map((s) => s.trim()).find((l) => l && !isOurOwn(l));
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

/**
 * Locate how to run the Python hamgoose server. Returns [cmd, args] or null.
 *
 * Order matters: the interpreter-checked `py -m hamgoose` first, because the
 * PATH fallback can never see the npm global shim that shadows the name.
 */
function resolveServer(py) {
  // 1) The interpreter we found can import hamgoose → `py -m hamgoose`.
  if (py && run(py, ["-c", "import hamgoose"]).code === 0) return [py, ["-m", "hamgoose"]];
  // 2) A hamgoose console script on PATH that is not this launcher.
  const onPath = which("hamgoose");
  if (onPath && !isOurOwn(onPath)) return [onPath, []];
  // 3) pip --user Scripts/bin fallback.
  if (py) {
    const base = run(py, ["-m", "site", "--user-base"]);
    if (base.code === 0) {
      const p = WIN
        ? path.join(base.out.trim(), "Scripts", "hamgoose.exe")
        : path.join(base.out.trim(), "bin", "hamgoose");
      if (existsSync(p)) return [p, []];
    }
  }
  return null;
}

function describe(cmdArgs) {
  return cmdArgs.length > 1 ? `${cmdArgs[0]} ${cmdArgs[1].join(" ")}` : cmdArgs[0];
}

/** Ensure the Python package is installed. Returns [cmd, args] or null. */
function ensureInstalled(quiet = false) {
  const py = findPython();
  let server = resolveServer(py);
  if (server) {
    if (!quiet) console.log(`hamgoose already installed (${describe(server)})`);
    return server;
  }
  if (!py) {
    console.error("Python 3.11+ not found. Install one (python.org or `uv`) and retry —\n" +
      "  or:  uv python install 3.12 && uv tool install git+" + REPO);
    return null;
  }
  if (!pipInstall(py)) return null;
  server = resolveServer(py);
  if (!quiet) console.log(server ? `Installed: ${describe(server)}` : "Installed (rerun, or check your Python install).");
  return server;
}

function printHelp() {
  console.log(`hamgoose npm launcher v${VERSION}
Runs the Python hamgoose server (github.com/cooked-ham/hamgoose).

Usage (after \`npm i -g @cooked-ham/hamgoose\`, or via \`npx -y @cooked-ham/hamgoose\`):
  hamgoose                    run the MCP stdio server (what Goose spawns)
  hamgoose install            install the Python package (idempotent)
  hamgoose register           install + register with Goose's config.yaml
  hamgoose unregister         remove hamgoose from Goose's config.yaml
  hamgoose help               show this message
  hamgoose --version          print version

Use with Goose: Add Extension (STDIO), Name "hamgoose", Command:
  hamgoose`);
}

const [, , ...args] = process.argv;
const first = args[0];

if (first === "help" || first === "--help" || first === "-h") {
  printHelp();
  process.exit(0);
}
if (first === "--version" || first === "-v") {
  console.log(VERSION);
  process.exit(0);
}
if (first === "install") {
  process.exit(ensureInstalled(false) ? 0 : 1);
}
if (first === "register" || first === "add" || first === "unregister" || first === "remove") {
  const server = ensureInstalled(false);
  if (!server) process.exit(1);
  const pyCmd = first === "add" ? "register" : first === "remove" ? "unregister" : first;
  console.log(pyCmd === "register" ? "Registering with Goose…" : "Updating Goose's config…");
  const r = launch(server, [pyCmd, ...args.slice(1)], true);
  process.exit(r.status ?? 1);
}
if (first) {
  // Anything else is NOT stdio-server mode: don't spawn anything.
  console.error(`Unknown command: ${first}\n\n`);
  printHelp();
  process.exit(2);
}

// No args: stdio server mode (Goose's extension command).
if (process.env.HAMGOOSE_LAUNCHER) {
  console.error("error: hamgoose launcher recursion guard tripped — refusing to spawn. " +
    "Check that `where hamgoose` / `which hamgoose` is not resolving to this launcher.");
  process.exit(3);
}
const server = ensureInstalled(true);
if (!server) process.exit(1);
const child = launch(server, [], false, { ...process.env, HAMGOOSE_LAUNCHER: "1" });
child.on("exit", (code) => process.exit(code ?? 0));
