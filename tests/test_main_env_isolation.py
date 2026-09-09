"""Importing the CLI module must not load `.env` into the process.

`load_dotenv()` used to run at `__main__` module scope. pytest imports every
test module during collection, and several tests import `social_info.__main__`,
so the real `.env` landed in `os.environ` before the first test even ran. Once
`.env` gained `APIFY_RELAY_URL` (2026-09-07) that silently redirected every
Apify fetcher test away from the URL its httpx mock was registered on: 7 tests
failed in the full suite while each passed on its own.

The env file is a CLI concern, so `main()` owns loading it.
"""
import subprocess
import sys


def test_importing_cli_module_does_not_load_dotenv(tmp_path):
    (tmp_path / ".env").write_text("SOCIAL_INFO_DOTENV_CANARY=leaked\n")

    probe = (
        "import os, social_info.__main__;"
        "print(os.environ.get('SOCIAL_INFO_DOTENV_CANARY', ''))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "", (
        "importing social_info.__main__ pulled .env into os.environ; "
        "load_dotenv() belongs inside main(), not at module scope"
    )
