#!/usr/bin/env python3
# Copied from agent-framework d9f9076 (skills/release-delivery/assets/deploy); do not edit here.
"""Checks for the deployment contracts v1 (references/deployment-contracts.md).

Standard library only (Python 3.11+). CI runs these before publishing, the engine before
deploying. Each validator returns a list of problems; an empty list means valid. Messages
name fields, never values, so they are safe to print.

    python3 contracts.py release-record FILE
    python3 contracts.py promotion STAGING_FILE PRODUCTION_FILE
    python3 contracts.py deployment-record FILE
    python3 contracts.py project-config deploy/release.toml
    python3 contracts.py settings deploy/settings.toml
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

RELEASE_SCHEMA = "af.release/v1"
DEPLOYMENT_SCHEMA = "af.deployment/v1"
STANDARD = 1

PROJECT = re.compile(r"[a-z][a-z0-9-]{0,62}")
COMMIT = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
HTTPS_URL = re.compile(r"https://[^\s\"'<>]+")
DIGEST_REF = re.compile(r"[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SERVICE = re.compile(r"[a-z][a-z0-9_-]{0,62}")
SETTING = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
# A Dockerfile build stage (`FROM ... AS <stage>`).
STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
ENVIRONMENT = re.compile(r"[a-z][a-z0-9-]{0,31}")
SCHEMA_VERSION = re.compile(r"[A-Za-z0-9._-]{1,128}")
GENERATOR = re.compile(r"(token_urlsafe|token_hex):([1-9][0-9]{0,3})")

RELEASE_FIELDS = {
    "schema",
    "project",
    "commit",
    "version",
    "created",
    "source",
    "ci_run",
    "images",
    "bundle",
    "schema_version",
    "rollback",
}
DEPLOYMENT_FIELDS = {
    "schema",
    "project",
    "environment",
    "result",
    "exit_code",
    "release",
    "commit",
    "version",
    "images",
    "schema_version",
    "previous_commit",
    "previous_schema_version",
    "backup",
    "trigger",
    "actor",
    "started",
    "finished",
    "engine",
}
RESULT_EXIT_CODES = {
    "deployed": {0},
    "unchanged": {0},
    "refused": {2},
    "failed": {1},
    "restored": {3},
    "restore_failed": {4},
    "stopped": {5},
    "busy": {75},
}
SWITCH_MODES = {"stop-start", "blue-green"}
BACKUP_MODES = {"required", "optional"}


def _port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536


def _match(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _exact_fields(document: object, expected: set[str], label: str) -> list[str]:
    if not isinstance(document, dict):
        return [f"{label} must be a JSON object"]
    problems = [f"{label}: missing field {name}" for name in sorted(expected - document.keys())]
    problems += [f"{label}: unknown field {name}" for name in sorted(document.keys() - expected)]
    return problems


def _images(images: object, label: str) -> list[str]:
    if not isinstance(images, dict) or not images:
        return [f"{label}: images must map at least one service to an image digest"]
    problems = []
    for service, reference in images.items():
        if not _match(SERVICE, service):
            problems.append(f"{label}: invalid service name in images")
        if not _match(DIGEST_REF, reference):
            problems.append(f"{label}: images.{service} must be a registry reference pinned by digest")
    return problems


def validate_release_record(record: object) -> list[str]:
    label = "release record"
    problems = _exact_fields(record, RELEASE_FIELDS, label)
    if problems or not isinstance(record, dict):
        return problems
    checks = [
        (record["schema"] == RELEASE_SCHEMA, f"schema must be {RELEASE_SCHEMA}"),
        (_match(PROJECT, record["project"]), "project must match [a-z][a-z0-9-]{0,62}"),
        (_match(COMMIT, record["commit"]), "commit must be a full 40-character lowercase SHA"),
        (record["version"] is None or _match(VERSION, record["version"]), "version must be null or vMAJOR.MINOR.PATCH"),
        (_match(TIME, record["created"]), "created must be UTC YYYY-MM-DDTHH:MM:SSZ"),
        (_match(HTTPS_URL, record["source"]), "source must be an https URL"),
        (_match(HTTPS_URL, record["ci_run"]), "ci_run must be an https URL"),
        (_match(DIGEST_REF, record["bundle"]), "bundle must be a registry reference pinned by digest"),
        (
            record["schema_version"] is None or _match(SCHEMA_VERSION, record["schema_version"]),
            "schema_version must be null or a plain identifier",
        ),
        (isinstance(record["rollback"], bool), "rollback must be true or false"),
    ]
    problems += [f"{label}: {message}" for ok, message in checks if not ok]
    problems += _images(record["images"], label)
    return problems


def validate_promotion(staging: dict, production: dict) -> list[str]:
    """A production record must promote exactly what staging ran, adding a version."""
    problems = [f"staging {p}" for p in validate_release_record(staging)]
    problems += [f"production {p}" for p in validate_release_record(production)]
    if problems:
        return problems
    for field in ("project", "commit", "images", "bundle", "schema_version"):
        if staging[field] != production[field]:
            problems.append(f"promotion: {field} differs from the staging record")
    if production["version"] is None:
        problems.append("promotion: a production record needs a version")
    return problems


def validate_deployment_record(record: object) -> list[str]:
    label = "deployment record"
    problems = _exact_fields(record, DEPLOYMENT_FIELDS, label)
    if problems or not isinstance(record, dict):
        return problems
    optional_commit = record["previous_commit"] is None or _match(COMMIT, record["previous_commit"])
    optional_schema = record["previous_schema_version"] is None or _match(
        SCHEMA_VERSION, record["previous_schema_version"]
    )
    checks = [
        (record["schema"] == DEPLOYMENT_SCHEMA, f"schema must be {DEPLOYMENT_SCHEMA}"),
        (_match(PROJECT, record["project"]), "project is invalid"),
        (_match(ENVIRONMENT, record["environment"]), "environment is invalid"),
        (record["result"] in RESULT_EXIT_CODES, "result is not a known result"),
        (_match(DIGEST, record["release"]), "release must be the record digest sha256:<64 hex>"),
        (_match(COMMIT, record["commit"]), "commit must be a full SHA"),
        (record["version"] is None or _match(VERSION, record["version"]), "version is invalid"),
        (
            record["schema_version"] is None or _match(SCHEMA_VERSION, record["schema_version"]),
            "schema_version is invalid",
        ),
        (optional_commit, "previous_commit must be null or a full SHA"),
        (optional_schema, "previous_schema_version is invalid"),
        (record["backup"] is None or isinstance(record["backup"], str), "backup must be null or a path"),
        (record["trigger"] in {"pull", "push"}, "trigger must be pull or push"),
        (isinstance(record["actor"], str) and bool(record["actor"]), "actor is required"),
        (_match(TIME, record["started"]), "started must be UTC"),
        (_match(TIME, record["finished"]), "finished must be UTC"),
        (isinstance(record["engine"], str) and bool(record["engine"]), "engine version is required"),
    ]
    problems += [f"{label}: {message}" for ok, message in checks if not ok]
    if record["result"] in RESULT_EXIT_CODES and record["exit_code"] not in RESULT_EXIT_CODES[record["result"]]:
        problems.append(f"{label}: exit_code does not match result")
    problems += _images(record["images"], label)
    return problems


def validate_project_config(config: dict) -> list[str]:
    label = "release.toml"
    problems = []
    if config.get("af_standard") != STANDARD:
        problems.append(f"{label}: af_standard must be {STANDARD}")
    if not _match(PROJECT, config.get("project")):
        problems.append(f"{label}: project is invalid")
    files = config.get("compose_files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
        problems.append(f"{label}: compose_files must list at least one file")
    images = config.get("images")
    if not isinstance(images, list) or not images:
        problems.append(f"{label}: at least one [[images]] entry is required")
    else:
        for image in images:
            if not isinstance(image, dict) or not _match(SERVICE, image.get("name")):
                problems.append(f"{label}: every image needs a valid name")
            elif "target" in image and not _match(STAGE, image["target"]):
                problems.append(f"{label}: an image target must be a build stage name")
    health = config.get("health")
    if not isinstance(health, dict) or not all(isinstance(health.get(k), str) for k in ("service", "path", "revision_key")):
        problems.append(f"{label}: health needs service, path and revision_key")
    elif not _port(health.get("port")) or not str(health["path"]).startswith("/"):
        problems.append(f"{label}: health needs an absolute path and the service's container port")
    for key in ("preflight", "migrate"):
        if key in config and not isinstance(config[key], (list, dict)):
            problems.append(f"{label}: {key} must be a command list or table")
    switch = config.get("switch", "stop-start")
    if switch not in SWITCH_MODES:
        problems.append(f"{label}: switch must be stop-start or blue-green")
    routes = config.get("routes", [])
    if switch == "blue-green" and not routes:
        problems.append(f"{label}: blue-green needs at least one [[routes]] entry")
    if not isinstance(routes, list):
        routes = [None]
    for route in routes:
        if (
            not isinstance(route, dict)
            or set(route) != {"host_setting", "service", "port"}
            or not _match(SETTING, route["host_setting"])
            or not _match(SERVICE, route["service"])
            or not _port(route["port"])
        ):
            problems.append(f"{label}: every route needs exactly host_setting, service, and port")
    if switch == "blue-green" and isinstance(health, dict) and routes and all(isinstance(r, dict) for r in routes):
        if health.get("service") not in {route.get("service") for route in routes}:
            problems.append(f"{label}: blue-green needs the health service to be a routed service")
    environments = config.get("environments")
    if not isinstance(environments, dict) or not environments:
        problems.append(f"{label}: at least one [environments.<name>] table is required")
    else:
        for name, environment in environments.items():
            if not _match(ENVIRONMENT, name):
                problems.append(f"{label}: invalid environment name")
                continue
            keep = environment.get("keep", 3)
            if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
                problems.append(f"{label}: environments.{name}.keep must be a positive integer")
            if environment.get("backup", "optional") not in BACKUP_MODES:
                problems.append(f"{label}: environments.{name}.backup must be required or optional")
        if "production" in environments and environments["production"].get("backup") != "required" and config.get("migrate"):
            problems.append(f"{label}: production with migrations needs backup = \"required\"")
    return problems


def validate_settings_manifest(manifest: dict) -> list[str]:
    label = "settings.toml"
    problems = []
    if manifest.get("af_standard") != STANDARD:
        problems.append(f"{label}: af_standard must be {STANDARD}")
    names: dict[str, list[str]] = {}
    for key in ("required", "optional", "placement", "secrets", "host_generated"):
        value = manifest.get(key, [])
        if not isinstance(value, list) or not all(_match(SETTING, name) for name in value):
            problems.append(f"{label}: {key} must list UPPER_CASE names")
            continue
        for name in value:
            names.setdefault(name, []).append(key)
    sources = ("placement", "secrets", "host_generated")
    for name, keys in names.items():
        if sum(key in sources for key in keys) > 1:
            problems.append(f"{label}: {name} has more than one source")
        if "required" in keys and "optional" in keys:
            problems.append(f"{label}: {name} is both required and optional")
    generators = manifest.get("generators", {})
    for name in manifest.get("host_generated", []):
        if not _match(GENERATOR, generators.get(name)):
            problems.append(f"{label}: {name} needs a generator such as token_urlsafe:32")
    for name in generators:
        if name not in manifest.get("host_generated", []):
            problems.append(f"{label}: generator for {name}, which is not host_generated")
    for pair in manifest.get("pairs", []):
        if not isinstance(pair, list) or len(pair) < 2 or not all(_match(SETTING, n) for n in pair):
            problems.append(f"{label}: every pair lists two or more names")
    for name, pattern in manifest.get("formats", {}).items():
        try:
            re.compile(pattern)
        except (re.error, TypeError):
            problems.append(f"{label}: formats.{name} is not a valid regular expression")
    secret_like = set(manifest.get("secrets", [])) | set(manifest.get("host_generated", []))
    for name in manifest.get("prod_assert", {}):
        if name in secret_like:
            problems.append(f"{label}: prod_assert cannot check secret {name}")
    defaults = manifest.get("defaults", {})
    if not isinstance(defaults, dict) or not all(_match(ENVIRONMENT, k) and isinstance(v, str) for k, v in defaults.items()):
        problems.append(f"{label}: defaults maps environment names to committed env files")
    return problems


def validate_defaults_file(text: str, manifest: dict) -> list[str]:
    """Committed defaults may hold only names whose source is the defaults file."""
    reserved = set()
    for key in ("placement", "secrets", "host_generated"):
        reserved.update(manifest.get(key, []))
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, _value = stripped.partition("=")
        if not separator or not _match(SETTING, name):
            problems.append(f"defaults line {number}: expected NAME=value")
        elif name in reserved:
            problems.append(f"defaults line {number}: {name} must not be committed (it has another source)")
    return problems


def _load(path: str) -> object:
    data = Path(path).read_bytes()
    if path.endswith(".toml"):
        return tomllib.loads(data.decode("utf-8"))
    return json.loads(data)


def main(argv: list[str]) -> int:
    validators = {
        "release-record": validate_release_record,
        "deployment-record": validate_deployment_record,
        "project-config": validate_project_config,
        "settings": validate_settings_manifest,
    }
    if len(argv) == 3 and argv[0] == "promotion":
        problems = validate_promotion(_load(argv[1]), _load(argv[2]))  # type: ignore[arg-type]
    elif len(argv) == 2 and argv[0] in validators:
        problems = validators[argv[0]](_load(argv[1]))  # type: ignore[arg-type]
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
