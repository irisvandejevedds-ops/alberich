#!/usr/bin/env python3
"""Alberich for your computer: statements, receipts and the rest, plus a copy of your phone's pouches.
Run it with:   python3 start.py        (Windows: py start.py)
The first run sets itself up (a private Python environment and a certificate for your phone to recognise); later runs just start."""
import asyncio
import os
import pathlib
import subprocess
import sys
import venv
import webbrowser

HERE = pathlib.Path(__file__).resolve().parent
VENV = HERE / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
UI_PORT = int(os.environ.get("ALBERICH_UI_PORT", "8100"))      # set these if something else already uses a port
SYNC_PORT = int(os.environ.get("ALBERICH_SYNC_PORT", "8121"))


def bootstrap():
    """Make the private environment and re-run inside it."""
    if sys.version_info < (3, 10):
        sys.exit("Alberich needs Python 3.10 or newer.")
    if not PY.exists():
        print("Setting up for the first time (this takes a minute)...")
        venv.EnvBuilder(with_pip=True).create(VENV)
        subprocess.check_call([str(PY), "-m", "pip", "install", "--quiet", "-r", str(HERE / "requirements.txt")])
    os.execv(str(PY), [str(PY), str(HERE / "start.py"), "--inside"]) if os.name != "nt" else sys.exit(subprocess.call([str(PY), str(HERE / "start.py"), "--inside"]))


def make_certificate(tls):
    """A self-signed certificate. The phone pins its fingerprint when you pair, so no certificate authority is involved."""
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    tls.mkdir(parents=True, exist_ok=True)
    if (tls / "cert.pem").exists():
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "alberich-companion")])
    now = datetime.now(timezone.utc)
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()) \
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650)).sign(key, hashes.SHA256())
    (tls / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    (tls / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(tls / "key.pem", 0o600)
    except OSError:
        pass


async def serve():
    import uvicorn
    import tzlocal
    from finapp.config import Config, set_config
    from finapp.main import create_app
    env = {"FINAPP_DATA_DIR": str(HERE / "data"), "FINAPP_TZ": os.environ.get("FINAPP_TZ") or tzlocal.get_localzone_name(), "FINAPP_REQUIRE_TS_HEADER": "0",
           "FINAPP_COOKIE_SECURE": "0", "FINAPP_SYNC_PORT": str(SYNC_PORT), "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "")}
    (HERE / "data" / "apk").mkdir(parents=True, exist_ok=True)
    make_certificate(HERE / "data" / "tls")
    set_config(Config(env))
    ui = create_app()                                   # the pages, on this computer only
    set_config(Config(dict(env, FINAPP_SYNC_ONLY="1", ANTHROPIC_API_KEY="")))
    companion = create_app()                            # the phone's door: the sync route only, over TLS, on your network
    worker = subprocess.Popen([sys.executable, "-m", "finapp.worker"], cwd=str(HERE), env=dict(os.environ, **env, PYTHONPATH=str(HERE)))   # reads statements and receipts in the background
    servers = [
        uvicorn.Server(uvicorn.Config(ui, host="127.0.0.1", port=UI_PORT, log_level="warning")),
        uvicorn.Server(uvicorn.Config(companion, host="0.0.0.0", port=SYNC_PORT, log_level="warning", ssl_keyfile=str(HERE / "data/tls/key.pem"), ssl_certfile=str(HERE / "data/tls/cert.pem"))),
    ]
    for s in servers:
        s.install_signal_handlers = lambda: None
    print(f"\nAlberich is running.\n  Open it here:  http://127.0.0.1:{UI_PORT}\n  Your phone connects on port {SYNC_PORT} (allow it if your firewall asks).\n  Press Ctrl+C to stop.\n")
    try:
        webbrowser.open(f"http://127.0.0.1:{UI_PORT}/settings/phone")
    except Exception:
        pass
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        worker.terminate()


if __name__ == "__main__":
    if "--inside" not in sys.argv:
        bootstrap()
    else:
        sys.path.insert(0, str(HERE))
        try:
            asyncio.run(serve())
        except KeyboardInterrupt:
            pass
