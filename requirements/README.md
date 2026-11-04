# Dependency lockfiles

| File | Installed by |
| --- | --- |
| `dashboard.lock` | `Dockerfile.dashboard` |
| `api.lock` | `Dockerfile.api` |
| `pipeline.lock` | `pipeline.yml` and the CI test job |
| `ci-tools.lock` | The CI scan job only |

Each lockfile pins every package, including indirect ones, with hashes, so pip refuses
anything that doesn't match. `tested-versions.txt` is `pip freeze` from the environment
the test suites ran on, which is what ties the lockfiles to tested versions.

`../requirements.txt` stays for Streamlit Community Cloud and local setup. To change a
version, install it, run the tests, then:

```
.venv/bin/python -m pip freeze --exclude-editable > requirements/tested-versions.txt
requirements/compile.sh
```

Upgrade Streamlit as its own change, and repeat the risk card and rerun timing checks
before and after.
