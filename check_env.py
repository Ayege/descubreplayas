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

"""Check that every variable in .env.example is set, without printing the values."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Every variable the project expects (must match .env.example).
REQUIRED_VARS = [
    "EE_PROJECT",
    "EE_SERVICE_ACCOUNT_JSON",
    "CMEMS_USERNAME",
    "CMEMS_PASSWORD",
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "TELEGRAM_BOT_TOKEN",
    "API_BASE_URL",
]


def main() -> int:
    env_path = Path(__file__).resolve().parent / ".env"
    loaded = load_dotenv(env_path)
    print(f".env loaded: {loaded} ({env_path})\n")

    missing = []
    for var in REQUIRED_VARS:
        value = os.environ.get(var, "")
        is_set = bool(value.strip())
        mark = "SET    " if is_set else "MISSING"
        print(f"  [{mark}] {var}")
        if not is_set:
            missing.append(var)

    print()
    if missing:
        print(f"{len(missing)} variable(s) missing: {', '.join(missing)}")
        return 1
    print("All variables are set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
