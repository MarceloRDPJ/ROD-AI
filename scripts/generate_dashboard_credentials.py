"""Generate dashboard secrets without reading or editing the real .env file."""

from __future__ import annotations

import getpass
import secrets
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis.api.dashboard_auth import hash_password  # noqa: E402


def main() -> int:
    username = input("Usuário do dashboard [marcelo]: ").strip() or "marcelo"
    password = getpass.getpass("Senha nova (mínimo 12 caracteres): ")
    confirmation = getpass.getpass("Repita a senha: ")
    if password != confirmation:
        print("As senhas não conferem.", file=sys.stderr)
        return 2
    try:
        encoded = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("\nCopie estas linhas para o .env do Pi; não as versione:\n")
    print("DASHBOARD_AUTH_ENABLED=true")
    print(f"DASHBOARD_USERNAME={username}")
    print(f"DASHBOARD_PASSWORD_HASH={encoded}")
    print(f"DASHBOARD_SESSION_SECRET={secrets.token_urlsafe(48)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
