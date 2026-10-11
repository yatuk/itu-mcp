# Releasing (maintainers)

How the release workflows are locked down, how to update a pin, and how the
PyPI upload is authenticated.

## What runs on a release

| Workflow | Trigger | What it does | Write access |
|---|---|---|---|
| `tests.yml` | push to `main`, pull requests | tests, ruff, pip-audit, bandit | none (`contents: read`) |
| `build-mcpb.yml` | tag `v*`, manual | builds one `.mcpb` per platform, attaches them to the release | only the `release` job has `contents: write` |
| `publish.yml` | release published, manual | uploads to PyPI, then publishes `server.json` to the MCP registry | the `registry` job has `id-token: write`; nothing can write to the repository |

A manual run of `build-mcpb.yml` builds the bundles and stops: the `release` job
only runs for a tag. Use it as a rehearsal after changing `scripts/build_mcpb.py`
or a pin.

## Pins

Everything a release job downloads and runs is fixed to an exact version, and where
the format allows it, to a checksum.

| What | Where | Pinned by |
|---|---|---|
| GitHub Actions | `.github/workflows/*.yml` | full commit SHA, release named in the trailing comment |
| `build`, `twine` | `publish.yml` | exact version |
| `mcp-publisher` | `publish.yml` (`MCP_PUBLISHER_VERSION`, `MCP_PUBLISHER_SHA256`) | release tag and SHA-256, checked before unpacking |
| CPython runtime in the bundle | `scripts/build_mcpb.py` (`RUNTIME_RELEASE`, `RUNTIME_PYTHON`, `RUNTIME_SHA256`) | release, version and one SHA-256 per platform, checked before unpacking |
| `@anthropic-ai/mcpb` packer | `scripts/build_mcpb.py` (`MCPB_CLI_VERSION`) | exact version |

`tests/test_release_pipeline.py` fails if an action is referenced by tag or branch,
if a workflow loses its read-only default, or if a runtime platform has no checksum.

### Updating an action

Resolve the release tag to a commit and paste the 40 character SHA, with the tag in
the comment:

```bash
gh api repos/actions/checkout/git/ref/tags/v5.1.0 --jq '.object.type + " " + .object.sha'
```

If the type is `commit`, that SHA is the pin. If it is `tag` (an annotated tag),
resolve once more:

```bash
gh api repos/actions/checkout/git/tags/<sha from above> --jq .object.sha
```

### Updating a downloaded tool

GitHub reports a SHA-256 for every release asset. Read it with the API and compare
it with the checksum file the project publishes in the same release, if there is one:

```bash
gh api repos/modelcontextprotocol/registry/releases/tags/v1.8.1 \
  --jq '.assets[] | select(.name=="mcp-publisher_linux_amd64.tar.gz") | .digest'

gh api repos/astral-sh/python-build-standalone/releases/tags/20260623 --paginate \
  --jq '.assets[] | select(.name|endswith("-install_only.tar.gz")) | [.name, .digest] | join(" ")'
```

Change the version and the checksum in the same commit. For the CPython runtime
that means all five platform digests.

## PyPI trusted publishing

`publish.yml` uploads with trusted publishing: PyPI issues a short-lived token to
this workflow only, and no API token is stored in the repository. It works once
the publisher is registered on PyPI:

1. Sign in to PyPI as an owner of the `itu-mcp` project.
2. Open <https://pypi.org/manage/project/itu-mcp/settings/publishing/>.
3. Under "Add a new publisher", choose GitHub and enter:
   - Owner: `yatuk`
   - Repository name: `itu-mcp`
   - Workflow name: `publish.yml`
   - Environment name: leave empty
4. Save. The publisher is now listed on that page.

Until that is done the "Publish to PyPI" step fails and nothing is uploaded. To
publish a release whose upload failed, run the workflow again from the Actions
tab ("Publish to PyPI", "Run workflow") after registering the publisher.

## Not done yet: builds from a hashed lock

`pyproject.toml` declares minimum versions. The bundle build and `pip install .`
therefore resolve the newest matching versions at build time, so two builds of the
same tag can contain different dependency versions. Fixing that takes:

1. A lock file with hashes, for example `uv pip compile pyproject.toml
   --generate-hashes --universal -o requirements.lock`. The bundle ships compiled
   wheels (`lxml`, `pydantic-core`) for three platforms, so the lock has to cover
   all of them.
2. `scripts/build_mcpb.py` installing the dependencies with
   `pip install --require-hashes -r requirements.lock --target ...` and then the
   project itself with `--no-deps`.
3. The same for the tools in `publish.yml` (`build`, `twine` and their own
   dependencies).
4. A scheduled job or Dependabot to refresh the lock, since a lock that is never
   updated keeps known-vulnerable versions in every bundle.

The PyPI package itself keeps version ranges; a lock applies to the artifacts this
repository builds, not to what `pip install itu-mcp` resolves on a user's machine.
