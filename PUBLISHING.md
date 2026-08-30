# Publishing to PyPI

Status: **packaging is ready** (metadata, classifiers, `LICENSE`, README;
build verified). The only remaining step is the upload, which needs the
approved PyPI org (`cookedham` or `cooked-ham`).

## Key fact: the org name does not change the package name

Whichever org gets approved, users still install with `pip install hamgoose`.
The PyPI org is just the *owner* of the project on pypi.org — so when the
approval lands, nothing in the code or README changes; only the account you
upload from does.

## When the org is approved

1. **Create the organization on PyPI** — pypi.org → *Your projects* →
   *Add organization* (or accept the invite if you created it from the org).
2. **Create the project** `hamgoose` under the org (the first upload can
   create it too).
3. **Add your user as a maintainer** of the project.
4. **Generate an API token** — user settings → *API tokens* → fine-grained
   (one project, `Release` scope). Store it as an env var, e.g. `PYPI_TOKEN`.

## Upload

```bash
python -m build                      # -> dist/hamgoose-0.1.1-*.whl + .tar.gz
python -m twine check dist/*         # metadata lint
python -m twine upload dist/*        # uses PYPI_TOKEN / ~/.pypirc
# uv alternative:  uv publish        (reads UV_PUBLISH_TOKEN)

git tag -a v0.1.1 -m "v0.1.1"
git push --tags
```

## Verify (and optional TestPyPI smoke test first)

```bash
# smoke test on TestPyPI:
python -m twine upload -r testpypi dist/*
pip install --dry-run --index-url https://test.pypi.org/simple/ hamgoose

# after the real upload:
pip install --dry-run hamgoose
```

## Versioning

- Bump `version` in `pyproject.toml` (single source of truth).
- Tag `v<version>` on the same commit so `pip install git+...@v<version>` pins work.
- Keep `npm/package.json` `version` in sync (npm launcher, same release).

## npm launcher (`@cooked-ham/hamgoose`)

The `npm/` directory holds the Node launcher (lets Node-first machines use
`npx @cooked-ham/hamgoose`). Publish with:

```bash
npm login
cd npm
npm publish --access public     # scoped packages default to private
```

It is a thin pointer to this repo — no code duplication, nothing to sync
beyond the version number.
