# Changelog

Notable changes per release, newest first. Format: Keep a Changelog; versions follow
semantic versioning.

## [Unreleased]

### Changed

- Releases move to direct delivery (Pakem ADR 0010): `pakem release` sends the signed
  source of the exact commit to the server, which builds and deploys it. CI runs on every
  push to `main`, tag, and pull request; the registry publishing workflows, the copied
  `af_publish.py`, and the release signing key are removed. Adopted with
  `pakem project adopt` (decisions in `.pakem/decisions.toml`).
