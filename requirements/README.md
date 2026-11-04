# Dependency lockfiles

| File | Installed by |
| --- | --- |
| `dashboard.lock` | `Dockerfile.dashboard` |
| `api.lock` | `Dockerfile.api` |
| `pipeline.lock` | `pipeline.yml` and the CI test job |
| `ci-tools.lock` | The CI scan job only |
| `ingest.lock` | `.venv-ingest` and the CI ingest job: scripts that download and decode images |

Each lockfile pins every package, including indirect ones, with hashes, so pip refuses
anything that doesn't match. `tested-versions.txt` is `pip freeze` from the environment
the test suites ran on, which is what ties the lockfiles to tested versions.

`../requirements.txt` stays for Streamlit Community Cloud and local setup. To change a
version, install it, run the tests, then:

```
.venv/bin/python -m pip freeze --exclude-editable > requirements/tested-versions.txt
requirements/compile.sh
```

## Two Pillow versions

Scripts that decode downloaded images need Pillow 13, and Streamlit 1.58 requires
`pillow<13`, so they can't share an environment. Keep them apart:

```
uv venv --python 3.11 .venv-ingest
uv pip install --python .venv-ingest/bin/python --require-hashes -r requirements/ingest.lock
.venv-ingest/bin/python scripts/fetch_species.py --dry-run --beach "Playa Bávaro"
.venv-ingest/bin/python -m tests.test_fetch_species
.venv-ingest/bin/python -m tests.test_image_pipeline
```

`scripts/_images.py` refuses to import under an older Pillow, so using the wrong
environment fails at once. `tests/test_workflows.py` checks that `ingest.lock` holds no
Streamlit, that no other lockfile holds Pillow 13, and that nothing in `dashboard/`,
`api/` or `pipeline/` imports Pillow or `scripts/_images.py`. The dashboard never decodes
untrusted images: it only serves our own re-encoded WebP files.

Upgrade Streamlit as its own change, and repeat the risk card and rerun timing checks
before and after.
