#!/usr/bin/env python3
# Copied from agent-framework d9f9076 (skills/release-delivery/assets/deploy); do not edit here.
"""Publish releases from CI (contracts v1, §1-3 and §8). Same script on GitHub and GitLab.

    af_publish.py publish  --repository R --commit SHA --source URL --ci-run URL
    af_publish.py promote  --repository R --commit SHA --version vX.Y.Z --ci-run URL --public-key KEY
    af_publish.py rollback --repository R --commit SHA --environment ENV --ci-run URL --public-key KEY
    af_publish.py bundle   --out bundle.tar        (deterministic deploy bundle only)

`publish` builds every image in deploy/release.toml once (multi-arch, revision label), packs
the deploy bundle, signs every digest, and moves `release-staging` to a new record, also
tagged `record-<commit>`. `promote` turns that verified record into the production record
(`release-production`, `record-<commit>-production`) without rebuilding. `rollback` re-points
an environment at an earlier commit's record with `rollback: true`.

Run from the repository root after logging in to the registry. Needs docker (buildx),
oras, and cosign; the signing key comes from COSIGN_PRIVATE_KEY and COSIGN_PASSWORD in the
environment, never from a file in the repository. Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import contracts

RELEASE_MEDIA = "application/vnd.af.release.v1+json"
BUNDLE_MEDIA = "application/vnd.af.bundle.v1.tar"
DEFAULT_PLATFORMS = ["linux/amd64", "linux/arm64"]
MAX_BUNDLE_FILE = 4 * 1024 * 1024

Runner = Callable[[list[str], Path | None], str]


class PublishError(Exception):
    pass


def log(message: str) -> None:
    print(f"af-publish: {message}", flush=True)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# How often a promotion looks again for a release that is still being published.
RECORD_POLL_SECONDS = 30


def run(arguments: list[str], cwd: Path | None = None) -> str:
    """Run a tool; return stdout and stderr. Arguments never contain secrets."""
    result = subprocess.run(arguments, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        raise PublishError(f"{arguments[0]} {arguments[1]} failed:\n{tail}")
    return result.stdout + result.stderr


def load_config(root: Path) -> dict:
    config = tomllib.loads((root / "deploy/release.toml").read_text(encoding="utf-8"))
    settings = tomllib.loads((root / "deploy/settings.toml").read_text(encoding="utf-8"))
    problems = contracts.validate_project_config(config) + contracts.validate_settings_manifest(settings)
    if problems:
        raise PublishError("; ".join(problems))
    return config


def schema_version(root: Path, config: dict) -> str | None:
    """The newest migration file name (without extension) matched by schema_version.glob."""
    if not config.get("migrate"):
        return None
    pattern = (config.get("schema_version") or {}).get("glob")
    if not pattern:
        raise PublishError("release.toml: projects with migrate need schema_version = { glob = ... }")
    matches = sorted(Path(path).name for path in glob.glob(str(root / pattern)))
    if not matches:
        raise PublishError("schema_version glob matches no migration files")
    return Path(matches[-1]).stem


def bundle_files(root: Path, config: dict) -> list[str]:
    """Compose files plus everything under deploy/, as sorted repository paths.

    Python bytecode caches are skipped: publishing imports deploy/af/contracts.py from the
    checkout, so they appear during the run, and they embed timestamps.
    """
    files = set(config["compose_files"])
    for path in (root / "deploy").rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_file():
            files.add(relative.as_posix())
    result = []
    for name in sorted(files):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise PublishError(f"bundle path escapes the repository: {name}")
        if any(part.startswith(".env") or part.startswith("._") or part == ".DS_Store" for part in relative.parts):
            raise PublishError(f"bundle would include a local or secret file: {name}")
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise PublishError(f"bundle entries must be regular files: {name}")
        if path.stat().st_size > MAX_BUNDLE_FILE:
            raise PublishError(f"bundle file is too large: {name}")
        result.append(name)
    return result


def build_bundle(root: Path, config: dict) -> bytes:
    """A byte-for-byte reproducible tar: sorted names, fixed owner, mode, and time."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in bundle_files(root, config):
            data = (root / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if os.access(root / name, os.X_OK) else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Publisher:
    def __init__(
        self,
        repository: str,
        *,
        runner: Runner = run,
        local: bool = False,
        work: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not re.fullmatch(r"[a-z0-9.:-]+(/[a-z0-9._-]+)+", repository):
            raise PublishError("repository must be <registry>/<path> in lower case")
        self.repository = repository
        self.runner = runner
        self.local = local  # loopback registry rehearsal: plain http, single-arch docker build
        self.work = work or Path(tempfile.mkdtemp(prefix="af-publish-"))
        self._signing_config: Path | None = None
        self.sleep = sleep
        self.clock = clock

    # -- tools -------------------------------------------------------------------------

    def _oras(self, *arguments: str) -> str:
        extra = ["--plain-http"] if self.local else []
        return self.runner(["oras", arguments[0], *extra, *arguments[1:]], self.work)

    def push_artifact(self, tags: list[str], artifact_type: str, name: str, content: bytes) -> str:
        path = self.work / name
        path.write_bytes(content)
        target = f"{self.repository}:{','.join(tags)}"
        output = self._oras("push", target, "--artifact-type", artifact_type, f"{name}:{artifact_type}")
        match = re.search(r"^Digest: (sha256:[0-9a-f]{64})$", output, re.M)
        if not match:
            raise PublishError("oras did not report a digest")
        return f"{self.repository}@{match[1]}"

    def pull_record(self, tag: str) -> tuple[str, dict]:
        destination = self.work / f"pull-{tag}"
        destination.mkdir(exist_ok=True)
        output = self._oras("pull", f"{self.repository}:{tag}", "--output", str(destination))
        match = re.search(r"^Digest: (sha256:[0-9a-f]{64})$", output, re.M)
        files = [path for path in destination.iterdir() if path.is_file()]
        if not match or len(files) != 1:
            raise PublishError(f"{tag} is not a single-file release record")
        record = json.loads(files[0].read_text(encoding="utf-8"))
        problems = contracts.validate_release_record(record)
        if problems:
            raise PublishError("; ".join(problems))
        return f"{self.repository}@{match[1]}", record

    def wait_for_record(self, tag: str, wait_seconds: int) -> tuple[str, dict]:
        """pull_record, retrying while the tag does not exist yet (a tag pushed before the
        merge's publish finished); any other failure is raised at once."""
        deadline = self.clock() + wait_seconds
        while True:
            try:
                return self.pull_record(tag)
            except PublishError as error:
                missing = "not found" in str(error).lower() or "manifest unknown" in str(error).lower()
                if not missing:
                    raise
                if self.clock() >= deadline:
                    raise PublishError(f"{tag} was not published; publish the commit on main first") from None
                log(f"{tag} is not published yet; checking again in {RECORD_POLL_SECONDS}s")
                self.sleep(RECORD_POLL_SECONDS)

    def signing_config(self) -> Path:
        if self._signing_config is None:
            path = self.work / "signing-config.json"
            self.runner(["cosign", "signing-config", "create", "--out", str(path)], None)
            self._signing_config = path
        return self._signing_config

    def sign(self, reference: str) -> None:
        if "COSIGN_PRIVATE_KEY" not in os.environ:
            raise PublishError("COSIGN_PRIVATE_KEY must hold the project's release signing key")
        arguments = ["cosign", "sign", "--yes", "--key", "env://COSIGN_PRIVATE_KEY", "--signing-config", str(self.signing_config())]
        if self.local:
            arguments.append("--allow-http-registry")
        self.runner([*arguments, reference], None)

    def verify(self, reference: str, public_key: Path) -> None:
        arguments = ["cosign", "verify", "--key", str(public_key), "--insecure-ignore-tlog=true"]
        if self.local:
            arguments.append("--allow-http-registry")
        self.runner([*arguments, reference], None)

    def build_image(self, root: Path, image: dict, commit: str) -> str:
        name = image["name"]
        tag = f"{self.repository}/{name}:sha-{commit}"
        dockerfile = str(root / image.get("dockerfile", "Dockerfile"))
        context = str(root / image.get("context", "."))
        labels = ["--build-arg", f"AF_REVISION={commit}", "--label", f"org.opencontainers.image.revision={commit}"]
        # One Dockerfile may hold several images as build stages; default the last stage.
        if image.get("target"):
            labels += ["--target", image["target"]]
        if self.local:
            self.runner(["docker", "build", "--quiet", *labels, "--file", dockerfile, "--tag", tag, context], None)
            self.runner(["docker", "push", "--quiet", tag], None)
            output = self.runner(["docker", "inspect", "--format", "{{json .RepoDigests}}", tag], None)
            prefix = f"{self.repository}/{name}@"
            matches = [ref for ref in json.loads(output.strip().splitlines()[-1]) if ref.startswith(prefix)]
            if len(matches) != 1:
                raise PublishError(f"docker reported no digest for {name} in {self.repository}")
            return matches[0]
        platforms = ",".join(image.get("platforms", DEFAULT_PLATFORMS))
        metadata = self.work / f"{name}-metadata.json"
        self.runner(
            ["docker", "buildx", "build", "--platform", platforms, *labels, "--file", dockerfile, "--tag", tag,
             "--metadata-file", str(metadata), "--push", context],
            None,
        )
        digest = json.loads(metadata.read_text(encoding="utf-8")).get("containerimage.digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise PublishError(f"buildx reported no digest for {name}")
        return f"{self.repository}/{name}@{digest}"

    # -- commands ----------------------------------------------------------------------

    def publish(self, root: Path, commit: str, source: str, ci_run: str) -> dict:
        config = load_config(root)
        images = {}
        for image in config["images"]:
            log(f"building {image['name']}")
            images[image["name"]] = self.build_image(root, image, commit)
            self.sign(images[image["name"]])
        bundle = self.push_artifact([f"bundle-{commit}"], BUNDLE_MEDIA, "bundle.tar", build_bundle(root, config))
        self.sign(bundle)
        record = {
            "schema": contracts.RELEASE_SCHEMA,
            "project": config["project"],
            "commit": commit,
            "version": None,
            "created": utc_now(),
            "source": source,
            "ci_run": ci_run,
            "images": images,
            "bundle": bundle,
            "schema_version": schema_version(root, config),
            "rollback": False,
        }
        self._push_record(record, [f"record-{commit}", "release-staging"])
        log("release-staging now points at this commit")
        return record

    def promote(self, commit: str, version: str, ci_run: str, public_key: Path, wait_seconds: int = 0) -> dict:
        reference, staging = self.wait_for_record(f"record-{commit}", wait_seconds)
        self.verify(reference, public_key)
        if staging["commit"] != commit:
            raise PublishError("the staging record belongs to another commit")
        production = dict(staging, version=version, created=utc_now(), ci_run=ci_run, rollback=False)
        problems = contracts.validate_promotion(staging, production)
        if problems:
            raise PublishError("; ".join(problems))
        self._push_record(production, [f"record-{commit}-production", "release-production"])
        log(f"release-production now points at {version}")
        return production

    def rollback(self, commit: str, environment: str, ci_run: str, public_key: Path) -> dict:
        suffix = "" if environment == "staging" else f"-{environment}"
        reference, earlier = self.pull_record(f"record-{commit}{suffix}")
        self.verify(reference, public_key)
        record = dict(earlier, created=utc_now(), ci_run=ci_run, rollback=True)
        self._push_record(record, [f"release-{environment}"])
        log(f"release-{environment} now points back at {commit[:12]}")
        return record

    def _push_record(self, record: dict, tags: list[str]) -> None:
        problems = contracts.validate_release_record(record)
        if problems:
            raise PublishError("; ".join(problems))
        reference = self.push_artifact(tags, RELEASE_MEDIA, "record.json", json.dumps(record, indent=2).encode())
        self.sign(reference)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="af_publish.py", description="Publish releases (contracts v1)")
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("bundle")
    bundle.add_argument("--out", type=Path, required=True)
    for name in ("publish", "promote", "rollback"):
        sub = commands.add_parser(name)
        sub.add_argument("--repository", required=True)
        sub.add_argument("--commit", required=True)
        sub.add_argument("--ci-run", required=True)
        sub.add_argument("--local", action="store_true", help="loopback registry rehearsal")
        if name == "publish":
            sub.add_argument("--source", required=True)
        else:
            sub.add_argument("--public-key", type=Path, required=True)
        if name == "promote":
            sub.add_argument("--version", required=True)
            sub.add_argument("--wait-minutes", type=int, default=0, choices=range(0, 121), metavar="0-120",
                             help="wait this long for the commit's release to be published")
        if name == "rollback":
            sub.add_argument("--environment", required=True)
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "bundle":
            args.out.write_bytes(build_bundle(root, load_config(root)))
            return 0
        if not contracts.COMMIT.fullmatch(args.commit):
            raise PublishError("--commit must be a full 40-character commit SHA")
        publisher = Publisher(args.repository, local=args.local)
        if args.command == "publish":
            publisher.publish(root, args.commit, args.source, args.ci_run)
        elif args.command == "promote":
            if not contracts.VERSION.fullmatch(args.version):
                raise PublishError("--version must be vMAJOR.MINOR.PATCH")
            publisher.promote(args.commit, args.version, args.ci_run, args.public_key, args.wait_minutes * 60)
        else:
            if not contracts.ENVIRONMENT.fullmatch(args.environment):
                raise PublishError("--environment is invalid")
            publisher.rollback(args.commit, args.environment, args.ci_run, args.public_key)
        return 0
    except PublishError as error:
        print(f"af-publish: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
