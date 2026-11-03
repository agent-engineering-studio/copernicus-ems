# package-name

PACKAGE_DESCRIPTION

[![ci](https://github.com/agent-engineering-studio/package-name/actions/workflows/ci.yml/badge.svg)](https://github.com/agent-engineering-studio/package-name/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/package-name)](https://pypi.org/project/package-name/)

## Using this template

1. `gh repo create agent-engineering-studio/<name> --public --template agent-engineering-studio/python-package-template --clone`
2. `./scripts/init.sh <name> "<one-line description>"` — renames the package,
   relocks, removes this section and the script.
3. On pypi.org, add a pending trusted publisher: owner
   `agent-engineering-studio`, repository `<name>`, workflow `release.yml`,
   environment `pypi`.
4. Bump the version in `pyproject.toml`, push a `vX.Y.Z` tag: `release.yml`
   tests, builds, publishes to PyPI and creates the GitHub release.

<!-- end template -->

## Install

```bash
pip install package-name
```

## Development

```bash
uv sync
make check
```

## License

[Apache-2.0](./LICENSE).
