from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT_DIR / ".venv"
REQUIREMENTS = ROOT_DIR / "requirements.txt"
LOCAL_ENV = ROOT_DIR / "config" / "user_keys.env"
EXAMPLE_ENV = ROOT_DIR / "config" / "user_keys.example.env"
INSTALL_STAMP = VENV_DIR / ".github_deep_search_requirements.sha256"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set up and start the GitHub Deep Search web app.")
    parser.add_argument("--host", default=os.getenv("GITHUB_DEEP_SEARCH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GITHUB_DEEP_SEARCH_PORT", "8001")))
    parser.add_argument("--strict-port", action="store_true", help="Fail instead of choosing another port.")
    parser.add_argument("--no-reload", action="store_true", help="Disable uvicorn auto-reload.")
    parser.add_argument("--reinstall", action="store_true", help="Force dependency installation.")
    return parser.parse_args()


def log(message: str) -> None:
    print(f"[github-deep-search] {message}", flush=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run_step(command: list[str]) -> None:
    subprocess.check_call(command, cwd=ROOT_DIR)


def requirements_digest() -> str:
    digest = hashlib.sha256()
    digest.update(REQUIREMENTS.read_bytes())
    digest.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode("ascii"))
    return digest.hexdigest()


def ensure_venv() -> Path:
    python = venv_python()
    if not python.exists():
        log("creating .venv")
        run_step([sys.executable, "-m", "venv", str(VENV_DIR)])
    return python


def ensure_dependencies(python: Path, reinstall: bool) -> None:
    wanted_digest = requirements_digest()
    current_digest = INSTALL_STAMP.read_text(encoding="utf-8").strip() if INSTALL_STAMP.exists() else ""
    if not reinstall and current_digest == wanted_digest:
        log("dependencies are already installed")
        return

    log("installing dependencies")
    run_step([str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    INSTALL_STAMP.write_text(wanted_digest + "\n", encoding="utf-8")


def ensure_local_env() -> None:
    if LOCAL_ENV.exists() or not EXAMPLE_ENV.exists():
        return
    shutil.copyfile(EXAMPLE_ENV, LOCAL_ENV)
    log("created config/user_keys.env from the example file")
    log("you can add API keys there later for better search quality")


def port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def choose_port(host: str, preferred_port: int, strict: bool) -> int:
    if not port_is_open(host, preferred_port):
        return preferred_port
    if strict:
        raise SystemExit(f"Port {preferred_port} is already in use.")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        port = int(sock.getsockname()[1])
    log(f"port {preferred_port} is busy; using {port} instead")
    return port


def start_web(python: Path, host: str, port: int, reload: bool) -> int:
    env = os.environ.copy()
    env["GITHUB_DEEP_SEARCH_HOST"] = host
    env["GITHUB_DEEP_SEARCH_PORT"] = str(port)
    env["GITHUB_DEEP_SEARCH_RELOAD"] = "1" if reload else "0"
    log(f"starting web app at http://{host}:{port}")
    return subprocess.call([str(python), "run_web.py"], cwd=ROOT_DIR, env=env)


def main() -> int:
    args = parse_args()
    python = ensure_venv()
    ensure_dependencies(python, args.reinstall)
    ensure_local_env()
    port = choose_port(args.host, args.port, args.strict_port)
    return start_web(python, args.host, port, reload=not args.no_reload)


if __name__ == "__main__":
    raise SystemExit(main())
