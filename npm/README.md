# @cooked-ham/hamgoose

**npm launcher for [hamgoose](https://github.com/cooked-ham/hamgoose)** — Factory-Droid-style
Mission orchestration for [Goose](https://goose-docs.ai): goal in, structured plan,
approval gate, isolated workers in git worktrees, dual validation, auto-correction.
Out: validated code.

## What is this package?

hamgoose itself is a **Python** stdio MCP server — this package is a thin,
dependency-free launcher so the install works from the Node world too. It finds
(or installs) the Python package and runs it. All mission logic lives in the
[GitHub repo](https://github.com/cooked-ham/hamgoose) — single source of truth.

## Usage

```bash
# either globally …
npm i -g @cooked-ham/hamgoose
hamgoose register        # install the Python package + register with Goose
hamgoose help            # show all commands

# …or ad-hoc via npx
npx @cooked-ham/hamgoose install     # install the Python package (idempotent)
npx @cooked-ham/hamgoose register    # install + register with Goose's config
npx -y @cooked-ham/hamgoose          # run the MCP stdio server (what Goose spawns)
```

## Use it as a Goose extension

`goose configure` → **Extensions → Add Extension** → Type `STDIO`, Name
`hamgoose`, Command:

```
npx -y @cooked-ham/hamgoose
```

(If you did `npm i -g @cooked-ham/hamgoose`, the command is simply `hamgoose`.)

…or the equivalent `extensions:` entry in `config.yaml`. Then in any repo:
`goose` → `/start_mission`.

Equivalent channels: `pip install git+https://github.com/cooked-ham/hamgoose.git`
or, once on PyPI, `pip install hamgoose` / `uvx hamgoose`.

## Requirements

- Node ≥ 18
- Python ≥ 3.11 (auto-detected: `python3`, `python`, `py -3`)
- `git` on PATH (for the `git+` install URL)

## License

MIT — see [LICENSE](https://github.com/cooked-ham/hamgoose/blob/main/LICENSE).
