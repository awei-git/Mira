#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CERT_DIR = ROOT / "data" / "certs" / "mira-bridge"


OPENSSL_TEMPLATE = """\
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = studio.local

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = studio.local
DNS.2 = localhost
DNS.3 = Mira.local
DNS.4 = mira.local
IP.1 = 127.0.0.1
"""


def _fingerprint(cert_path: Path) -> str:
    der = subprocess.check_output(["openssl", "x509", "-in", str(cert_path), "-outform", "der"])
    return hashlib.sha256(der).hexdigest().upper()


def generate(cert_dir: Path, *, days: int = 397, force: bool = False) -> dict[str, str]:
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"
    fp_path = cert_dir / "server.sha256"
    if (cert_path.exists() or key_path.exists()) and not force:
        fingerprint = fp_path.read_text(encoding="utf-8").strip() if fp_path.exists() else _fingerprint(cert_path)
        return {"cert": str(cert_path), "key": str(key_path), "fingerprint": fingerprint, "created": "false"}
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as fh:
        fh.write(OPENSSL_TEMPLATE)
        cnf = Path(fh.name)
    try:
        subprocess.check_call(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-days",
                str(days),
                "-config",
                str(cnf),
            ]
        )
    finally:
        try:
            cnf.unlink()
        except OSError:
            pass
    key_path.chmod(0o600)
    cert_path.chmod(0o644)
    fingerprint = _fingerprint(cert_path)
    fp_path.write_text(fingerprint + "\n", encoding="utf-8")
    return {"cert": str(cert_path), "key": str(key_path), "fingerprint": fingerprint, "created": "true"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Mira local bridge TLS certificate.")
    parser.add_argument("--cert-dir", type=Path, default=DEFAULT_CERT_DIR)
    parser.add_argument("--days", type=int, default=397)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = generate(args.cert_dir, days=args.days, force=args.force)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
