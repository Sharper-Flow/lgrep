# Vision deploy command

`scripts/deploy_vision.py` deploys a tagged `lgrep` release to the local Vision MCP
manager and proves the deployment is healthy before reporting success.

## What it does

1. Validates that it is running from a clean, non-worktree checkout of `main`.
2. Resolves the release tag (``--tag`` or the exact tag at `HEAD`).
3. Downloads the GitHub Release wheel for the tag.
4. Validates `~/.config/vision/servers.yaml` with `vision config validate`.
5. Installs the wheel into the uv tool runtime with `uv tool install --reinstall`.
6. Restarts the Vision user service with `systemctl --user restart vision.service`.
7. Waits for Vision health, allowing one bounded retry after the restart.
8. Confirms the installed `lgrep --version` matches the selected release.
9. Runs two non-destructive MCP health checks through Vision:
   - `prune_orphans`
   - `prune_symbols`

Each health check must return a structured result with `dry_run: true` and a
string `refused_reason`. The command exits nonzero if any step fails.

## Usage

Run from a clean checkout of the default branch after the release tag exists on
GitHub:

```bash
python scripts/deploy_vision.py
```

Use an explicit tag:

```bash
python scripts/deploy_vision.py --tag v3.2.5
```

Validate context without installing or restarting:

```bash
python scripts/deploy_vision.py --dry-run
```

Point at a non-default Vision config:

```bash
python scripts/deploy_vision.py --vision-config /path/to/servers.yaml
```

## Safety invariants

The command refuses to run unless all of the following are true:

- The current directory is inside a git work tree.
- The current branch is `main`.
- The working tree has no uncommitted changes.
- The checkout is not a git worktree (`--git-dir` resolves to `--git-common-dir`).

A `--dry-run` still enforces the context checks. The actual wheel download,
install, restart, and health checks are skipped.

## Retry behavior

After restarting the Vision service, the command checks `vision health` once
immediately and once more after a short delay if the first check fails. A
persistent failure exits nonzero and reports the failing step.

## Exit codes

| Exit code | Meaning |
|---|---|
| `0` | Deploy context valid and all health checks passed. |
| `1` | Unsafe context, failed command, or health check failure. |
| `2` | Argument-parsing error (argparse default). |

## Why non-destructive health checks

`prune_orphans` and `prune_symbols` are normally destructive cache cleanup
tools. When called over MCP without the `LGREP_ALLOW_DESTRUCTIVE_MCP` grant,
they return a preview result that includes `refused_reason`. The deploy command
uses these calls to prove the Vision-managed lgrep server is responsive and
returning the expected safe behavior, without deleting any data.
