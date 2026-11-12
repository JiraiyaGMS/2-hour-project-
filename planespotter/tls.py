"""A secure (https) address for the map on your home network.

Phones only let a web page use the camera, compass, microphone and share
sheet over https. planespotter makes its own certificate for that (with the
openssl tool that comes with macOS). Nobody vouches for a home-made
certificate, so the first time you open the https address your phone warns
you and you confirm once that you trust it; the README walks through it.
"""

from __future__ import annotations

import logging
import shutil
import ssl
import subprocess
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG = """[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = planespotter
O = planespotter on your computer
[v3]
subjectAltName = {names}
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
"""


def ensure_certificate(folder: Path, addresses: list[str]) -> tuple[Path, Path] | None:
    """(certificate, key) covering these addresses, made once and remade if an address changes."""
    folder.mkdir(parents=True, exist_ok=True)
    cert, key, names_file = folder / "cert.pem", folder / "key.pem", folder / "addresses.txt"
    names = ["localhost", "127.0.0.1", *[a for a in addresses if a]]
    wanted = ",".join(sorted(set(names)))
    if cert.is_file() and key.is_file() and names_file.is_file() and names_file.read_text().strip() == wanted:
        return cert, key
    openssl = shutil.which("openssl")
    if not openssl:
        log.info("no openssl, so no https address (the AR sky view needs it on phones)")
        return None
    san = ",".join(f"IP:{n}" if n.replace(".", "").isdigit() else f"DNS:{n}" for n in sorted(set(names)))
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as fh:
        fh.write(CONFIG.format(names=san))
        conf = fh.name
    try:
        subprocess.run(
            [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "397",
             "-keyout", str(key), "-out", str(cert), "-config", conf],
            check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("couldn't make an https certificate: %s", exc)
        return None
    finally:
        Path(conf).unlink(missing_ok=True)
    key.chmod(0o600)
    names_file.write_text(wanted)
    return cert, key


class TLSServer(ThreadingHTTPServer):
    """An https server. The encryption handshake happens in each request's own thread, so one
    slow or confused visitor (like a phone still deciding whether to trust us) holds nobody up."""

    daemon_threads = True

    def __init__(self, address, handler, cert: Path, key: Path):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(cert), str(key))
        super().__init__(address, handler)

    def get_request(self):
        sock, addr = self.socket.accept()
        sock.settimeout(30)
        return self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def finish_request(self, request, client_address):
        try:
            request.do_handshake()
        except (ssl.SSLError, OSError):
            return  # e.g. the browser hasn't been told to trust the certificate yet
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        log.debug("https request from %s failed", client_address[0], exc_info=True)

