# Offline CI

The first CI stage is intentionally isolated from the robot and from deployment infrastructure.

## What runs

- Repository policy checks over `git ls-files`, including forbidden paths, binary formats, large files, private keys, credentials, identity literals, real environment files, links, and submodules.
- Historical verification of the sanitized import commit and its snapshot manifests.
- Python AST parsing on Python 3.8 and Python 3.12 without importing the patrol or gateway entry points.
- Standard-library tests covering the existing pure-Python inspection-map simulation helpers.
- Source-level tests for the sanitized patrol identity configuration and Fast DDS UDP-only profile.

## Safety boundary

- GitHub-hosted runners only. `self-hosted` runners are forbidden by policy.
- Workflow permissions are read-only and checkout credentials are discarded.
- No repository or environment secrets are read.
- No SSH, device mounts, host networking, artifact upload, release, push, deployment, or robot access is present.
- `ROS_LOCALHOST_ONLY=1` and an isolated ROS domain are set defensively, although the first-stage jobs do not start ROS nodes.

## Deferred ROS2 tests

Twenty-six existing tests require ROS2 Foxy, Nav2, rosbridge, generated message packages, or the ROS launch system. Foxy is end-of-life and is not available as a supported native environment on current GitHub-hosted runners. Those tests are deliberately excluded until a fixed-digest, isolated Foxy image or a dedicated non-robot runner is reviewed. Such a runner must not mount `/dev`, use host networking, or share the robot ROS domain.

## Local equivalent

```text
python tools/ci/verify_repository.py
python -m unittest discover -s tests -v
```

The root `SHA256SUMS` and `docs/snapshot-20260714/` records describe the initial sanitized import. CI verifies those records against commit `8ffd2315b23194f89b660f35305a477f8ba4c008`, so later source development does not rewrite snapshot history.

## Required GitHub protection

The workflow hash and policy verifier protect against accidental changes, but they live in the same repository as the workflow. Configure a GitHub ruleset for `master` and `ci-cd-experiment` that requires pull requests, successful `Repository policy` and `Offline Python` checks, and CODEOWNER approval for `.github/workflows/`, `.github/CODEOWNERS`, `.gitignore`, `SHA256SUMS`, `docs/snapshot-20260714/`, and `tools/ci/`. Do not permit force pushes or branch deletion on protected branches.
