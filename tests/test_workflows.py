# Copyright 2026 Ayesha Yege
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline checks that the safety nets around merging and deploying stay in place.

Run from the repo root:

    python -m tests.test_workflows
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOWS = sorted((_REPO / ".github" / "workflows").glob("*.yml"))
_FULL_SHA = re.compile(r"[^@\s]+@[0-9a-f]{40}")
_OFFLINE_SUITES = {
    "beaches", "conoce", "dispatch", "drift", "fetch_species", "images", "profiles", "risk_overlay", "surf", "workflows",
}
_LIVE_SUITES = {"detect", "ocean", "store"}
_LOCKS = ("api", "ci-tools", "dashboard", "pipeline")


def _check(condition: bool, message: str) -> None:
    assert condition, message


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _workflow(name: str) -> dict:
    return _load(_REPO / ".github" / "workflows" / f"{name}.yml")


def test_every_workflow_has_least_privilege() -> None:
    _check(len(_WORKFLOWS) >= 4, "expected the four workflows")
    for path in _WORKFLOWS:
        permissions = _load(path).get("permissions", "missing")
        _check(permissions in ({}, {"contents": "read"}), f"{path.name}: permissions are {permissions}")


def test_every_action_is_pinned_to_a_full_sha() -> None:
    for path in _WORKFLOWS:
        for name, job in _load(path)["jobs"].items():
            for step in job.get("steps", []):
                if "uses" in step:
                    _check(_FULL_SHA.fullmatch(step["uses"]) is not None, f"{path.name}/{name}: {step['uses']}")


def test_every_job_has_a_timeout() -> None:
    for path in _WORKFLOWS:
        for name, job in _load(path)["jobs"].items():
            if "uses" not in job:
                _check("timeout-minutes" in job, f"{path.name}/{name} has no timeout")


def test_ci_runs_before_merge_with_only_offline_suites() -> None:
    triggers = _workflow("ci")[True]
    _check("pull_request" in triggers and "workflow_call" in triggers, "CI must run on pull requests and for deploys")
    text = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    listed = set(re.search(r"for suite in ([^;]+);", text).group(1).split())
    _check(listed == _OFFLINE_SUITES, f"suites: {listed}")
    _check(not listed & _LIVE_SUITES, "live suites must stay out of CI")
    on_disk = {p.stem.removeprefix("test_") for p in (_REPO / "tests").glob("test_*.py")}
    _check(on_disk - listed == _LIVE_SUITES, f"every offline suite must run in CI: {on_disk - listed}")
    for scanner in ("ruff", "bandit", "pip-audit", "gitleaks"):
        _check(scanner in text, f"CI must run {scanner}")


def test_deploy_is_gated() -> None:
    jobs = _workflow("deploy")["jobs"]
    _check(jobs["test"]["uses"] == "./.github/workflows/ci.yml", "deploy must call CI")
    for name in ("deploy-api", "deploy-dashboard"):
        job = jobs[name]
        _check({"test", "changes"} <= set(job["needs"]), f"{name} must wait for the tests")
        _check("refs/heads/main" in job["if"], f"{name} must deploy only from main")
        commands = [step.get("run", "") for step in job["steps"]]
        order = [i for i, run in enumerate(commands) if "--no-traffic" in run or "curl" in run or "--to-latest" in run]
        _check(len(order) == 3 and "--no-traffic" in commands[order[0]], f"{name}: deploy without traffic first")
        _check("curl" in commands[order[1]] and "--to-latest" in commands[order[2]], f"{name}: check, then promote")


def test_deploy_checks_static_files() -> None:
    text = (_REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    _check("/app/static/beaches/.gitkeep" in text, "the smoke check must catch the static-file failure")
    _check((_REPO / "dashboard" / "static" / "beaches" / ".gitkeep").is_file(), "the file the smoke check fetches")


def test_summary_reports_failure() -> None:
    summary = _workflow("deploy")["jobs"]["summary"]
    _check(summary["if"] == "always()", "the summary must always run")
    run = summary["steps"][0]["run"]
    _check("exit 1" in run and "complete" not in run, "the summary must not claim success after a failure")


def test_images_install_locked_dependencies() -> None:
    for name, lock in (("Dockerfile.dashboard", "dashboard"), ("Dockerfile.api", "api")):
        text = (_REPO / name).read_text(encoding="utf-8")
        _check(f"requirements/{lock}.lock" in text and "--require-hashes" in text, f"{name} must install its lockfile")
        _check("requirements.txt" not in text, f"{name} must not read requirements.txt")
    for lock in _LOCKS:
        text = (_REPO / "requirements" / f"{lock}.lock").read_text(encoding="utf-8")
        _check("--hash=sha256:" in text, f"{lock}.lock must have hashes")
    pipeline = (_REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    _check("--require-hashes -r requirements/pipeline.lock" in pipeline, "the pipeline holds production secrets")


def test_local_and_production_security_settings_match() -> None:
    local = (_REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    docker = (_REPO / "Dockerfile.dashboard").read_text(encoding="utf-8")
    for setting in ("enableXsrfProtection = true", "enableCORS = true", "enableStaticServing = true"):
        _check(setting in local and setting in docker, f"both configs need {setting}")


def main() -> int:
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} workflow tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
