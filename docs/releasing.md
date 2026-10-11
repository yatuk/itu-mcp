# Releasing (maintainers)

How the release workflows are locked down, how to update a pin, and how to move the
PyPI upload from an API token to trusted publishing.

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

## Moving PyPI to trusted publishing

Today `publish.yml` uploads with the `PYPI_API_TOKEN` repository secret. Trusted
publishing replaces that long-lived token with a short-lived one that PyPI issues
to this workflow only. The order matters: the PyPI side first, the workflow second.
If the workflow is changed first, the upload fails.

1. Sign in to PyPI as an owner of the `itu-mcp` project.
2. Open <https://pypi.org/manage/project/itu-mcp/settings/publishing/>.
3. Under "Add a new publisher", choose GitHub and enter:
   - Owner: `yatuk`
   - Repository name: `itu-mcp`
   - Workflow name: `publish.yml`
   - Environment name: leave empty
4. Save. The publisher is now listed on that page.
5. In `.github/workflows/publish.yml`, job `pypi`:
   - add `id-token: write` under the job's `permissions:`
   - delete the two lines `with:` and `password: ${{ secrets.PYPI_API_TOKEN }}`
     of the "Publish to PyPI" step
6. Publish the next release and check that the upload step succeeds.
7. Then delete the API token on PyPI (Account settings, API tokens) and the
   `PYPI_API_TOKEN` secret in the repository settings.

After step 5 the job looks like this:

```yaml
  pypi:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      # checkout, setup-python, build and twine check stay as they are
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
```

Optional hardening: create a GitHub environment named `pypi` with required
reviewers, add `environment: pypi` to the job, and enter `pypi` as the environment
name in step 3. A release then waits for an approval before it can upload.

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
