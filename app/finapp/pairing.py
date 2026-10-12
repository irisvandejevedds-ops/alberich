"""Pairing a phone: which addresses it can reach this computer at, the pinned fingerprint of the home-network certificate, and the link/QR that carries them."""
import hashlib
import ipaddress
import shutil
import socket
import ssl
import subprocess
from urllib.parse import quote


def pin(cfg):
    """SHA-256 of the self-signed certificate the companion listener uses (hex), or None if it has not been made."""
    p = cfg.tls_dir / "cert.pem"
    if not p.is_file():
        return None
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(p.read_text())).hexdigest()


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip and not ip.startswith("127.") else None
    except OSError:
        return None


def tailscale_ip():
    """This computer's Tailscale address (100.x.y.z) if Tailscale is installed and connected, else None."""
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=3).stdout.split()
        ip = ipaddress.ip_address(out[0]) if out else None
        return str(ip) if ip and ip in ipaddress.ip_network("100.64.0.0/10") else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def computer_name():
    return socket.gethostname()[:40]


def endpoints(cfg, host=""):
    """-> [{url, pin, label}] in the order a phone should try them: home network first, then any address the page was opened on (Tailscale), then extras."""
    out, fp = [], pin(cfg)
    ip = lan_ip()
    if ip and fp:
        out.append({"url": f"https://{ip}:{cfg.sync_port}", "pin": fp, "label": "home Wi-Fi"})
    tip = tailscale_ip()
    if tip and fp:
        out.append({"url": f"https://{tip}:{cfg.sync_port}", "pin": fp, "label": "Tailscale"})
    h = (host or "").split(":")[0]
    if h.endswith(".ts.net"):
        out.append({"url": "https://" + host, "pin": None, "label": "Tailscale"})
    for u in cfg.sync_urls:
        out.append({"url": u, "pin": fp if u.startswith("https://") and u.split("//")[1].split("/")[0].split(":")[0].replace(".", "").isdigit() else None, "label": "extra"})
    return out


def link(cfg, urls, token, name=""):
    """alberich://pair?v=1&name=..&key=..&e=<url>#<pin>&e=..  The key is shown only here; the phone stores it."""
    parts = ["v=1", "name=" + quote(name, safe=""), "key=" + quote(token, safe="")]
    for u in urls:
        parts.append("e=" + quote(u["url"] + ("#" + u["pin"] if u["pin"] else ""), safe=""))
    return "alberich://pair?" + "&".join(parts)


def qr_svg(text):
    import segno
    return segno.make(text, error="m").svg_inline(scale=5, dark="#16351F", light="#FBFEF2", border=2, omitsize=False)
