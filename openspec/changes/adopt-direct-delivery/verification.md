# Verification

Change `adopt-direct-delivery`, 2026-10-22 and 2026-10-23.

| Check | Result |
|---|---|
| Adoption (`pakem project adopt`, `pakem project check --gaps`) | 9 gaps answered from the repository's own files; `contract.image` provisional (the CI contract test keeps its pinned gateway image) |
| `./scripts/check` on the adoption and release branches (throwaway PostgreSQL 16) | passed: lock, format, lint, pyright, 250 tests (2 opt-in contract tests skipped), compose configs, deploy manifests, OpenSpec; actionlint clean; `openspec validate --strict` valid |
| Host (`pakem setup host msi`, the owner's terminal) | production switched from the registry to direct delivery with placement, secrets, and backups kept; release port 2223; the device key reached the receiver |
| CI on the merge commit `e189b918292d` | passed on GitHub-hosted runners |
| `v0.2.0` (`pakem release promote v0.2.0 --production`, device key only) | `app` and `backup` images built on the server; pre-migration backup; `/health` reported `e189b918292d`; result `deployed`; tag `v0.2.0` pushed after the deployment |
| Owner checks (2026-10-23) | the deployment notice arrived; the bot replies correctly |

## Not verified yet

- Deleting the `COSIGN_*` repository secrets (task 2.3, owner).
