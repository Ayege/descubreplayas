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

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOWS = sorted((_REPO / ".github" / "workflows").glob("*.yml"))
_FULL_SHA = re.compile(r"[^@\s]+@[0-9a-f]{40}")
_OFFLINE_SUITES = {
    "afai", "beaches", "conoce", "dispatch", "drift", "evaluate", "images", "map_perf", "profiles", "reports",
    "risk_overlay", "surf", "workflows",
}
# These import scripts/_images.py, so they need Pillow 13 and the ingest lockfile.
_INGEST_SUITES = {"fetch_species", "image_pipeline", "beach_photos", "review_photos"}
_LIVE_SUITES = {"detect", "ocean", "store"}
_LOCKS = ("api", "ci-tools", "dashboard", "ingest", "pipeline")
_MAIN_LOCKS = ("api", "dashboard", "pipeline")


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
    main, ingest = (set(found.split()) for found in re.findall(r"for suite in ([^;]+);", text))
    _check(main == _OFFLINE_SUITES, f"suites: {main}")
    _check(ingest == _INGEST_SUITES, f"ingest suites: {ingest}")
    _check(not (main | ingest) & _LIVE_SUITES, "live suites must stay out of CI")
    on_disk = {p.stem.removeprefix("test_") for p in (_REPO / "tests").glob("test_*.py")}
    _check(on_disk - main - ingest == _LIVE_SUITES, f"every offline suite must run in CI: {on_disk - main - ingest}")
    jobs = _workflow("ci")["jobs"]
    install = [step["run"] for step in jobs["ingest"]["steps"] if "pip install" in step.get("run", "")]
    _check(install == ["pip install --require-hashes -r requirements/ingest.lock"], "ingest job must use its own lockfile")
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


def _lock_version(lock: str, package: str) -> str | None:
    text = (_REPO / "requirements" / f"{lock}.lock").read_text(encoding="utf-8")
    match = re.search(rf"^{package}==([0-9][^ ]*)", text, re.MULTILINE)
    return match.group(1) if match else None


def test_two_pillow_versions_stay_apart() -> None:
    _check(_lock_version("ingest", "streamlit") is None, "ingest.lock must not hold Streamlit")
    _check((_lock_version("ingest", "pillow") or "0").split(".")[0] == "13", "ingest.lock must hold Pillow 13")
    for lock in _MAIN_LOCKS:
        version = _lock_version(lock, "pillow")
        _check(version is None or int(version.split(".")[0]) < 13, f"{lock}.lock must not hold Pillow 13")
    header = (_REPO / "requirements" / "ingest.lock").read_text(encoding="utf-8").split("httpx==")[0]
    _check("never decodes untrusted images" in header, "ingest.lock must say why it exists")
    for folder in ("dashboard", "api", "pipeline"):
        for path in (_REPO / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("from PIL", "import PIL", "scripts._images", "scripts import _images"):
                _check(forbidden not in text, f"{path.name} must not use {forbidden}")


def test_local_and_production_security_settings_match() -> None:
    local = (_REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    docker = (_REPO / "Dockerfile.dashboard").read_text(encoding="utf-8")
    for setting in ("enableXsrfProtection = true", "enableCORS = true", "enableStaticServing = true"):
        _check(setting in local and setting in docker, f"both configs need {setting}")


def _run_entrypoint(env_extra: dict[str, str]) -> str:
    """Run the real entrypoint against a stand-in Streamlit page, with a stub in place
    of the streamlit command, and return the page it produced.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        page = root / "index.html"
        page.write_text(
            '<!doctype html><html lang="en"><head>\n<meta name="viewport"\n content="width=device-width">'
            "<title>Streamlit</title></head><body></body></html>",
            encoding="utf-8",
        )
        stub = root / "bin" / "streamlit"
        stub.parent.mkdir()
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        env = {"PATH": f"{stub.parent}:{os.environ['PATH']}", "STREAMLIT_INDEX_HTML": str(page), **env_extra}
        bash = shutil.which("bash")
        _check(bash is not None, "bash is needed to run the entrypoint")
        result = subprocess.run(  # noqa: S603 - fixed local script, test only
            [bash, str(_REPO / "docker-entrypoint-dashboard.sh")], env=env, capture_output=True, text=True, timeout=60,
        )
        _check(result.returncode == 0, f"entrypoint failed: {result.stderr[-300:]}")
        return page.read_text(encoding="utf-8")


def _inline_scripts(page: str) -> list[str]:
    return [body for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", page, re.DOTALL) if "src=" not in attrs]


def test_entrypoint_injects_working_scripts() -> None:
    page = _run_entrypoint({"GA_MEASUREMENT_ID": "G-TEST123"})
    scripts = _inline_scripts(page)
    _check(len(scripts) == 2, f"expected the GA and canonical scripts, found {len(scripts)}")
    canonical = next(s for s in scripts if "canonical" in s)
    for piece in ('document.createElement("link")', 'setAttribute("rel", "canonical")', '"og:url"'):
        _check(piece in canonical, f"the shell must not strip the quotes from {piece}")
    _check("gtag('config', 'G-TEST123')" in page, "the GA snippet keeps its quotes and ID")
    _check(page.count('<meta property="og:url"') == 1 and '<html lang="es-DO">' in page, "the other tags are injected")
    node = shutil.which("node")
    if node:
        for script in scripts:
            check = subprocess.run([node, "--check", "-"], input=script, capture_output=True, text=True, timeout=30)  # noqa: S603
            _check(check.returncode == 0, f"injected script does not parse: {check.stderr[:200]}")
    plain = _run_entrypoint({})
    _check("googletagmanager" not in plain and len(_inline_scripts(plain)) == 1, "no GA without an ID")


def main() -> int:
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} workflow tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
