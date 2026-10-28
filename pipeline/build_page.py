"""Inject build/outlook.json into the page, then encrypt it for the password gate.

    uv run python pipeline/build_page.py              # build/outlook.html only (local preview)
    PAGE_PASSWORD=... uv run python pipeline/build_page.py --publish
                                                      # + docs/outlook/{index.html,page.enc}

The plaintext page carries the data the gate protects: it lives in build/ (gitignored) and is
never committed. Encryption format as ds-aa-bfa-drought/scripts/hnrp_2027/encrypt_page.py:
base64(salt[16] | iv[12] | AES-256-GCM ciphertext+tag), key = PBKDF2-HMAC-SHA256(password,
salt, 300 000 iterations); site/gate.html decrypts it in the browser.
"""

import argparse
import base64
import os
import shutil
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from outlook.config import BUILD, ROOT

ITER = 300_000
MARK = "/*__DATA__*/null"


def encrypt(plain: bytes, password: str) -> str:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(
        password.encode()
    )
    return base64.b64encode(salt + iv + AESGCM(key).encrypt(iv, plain, None)).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true", help="write the encrypted page to docs/")
    args = ap.parse_args()

    tpl = (ROOT / "site" / "app.html").read_text()
    if tpl.count(MARK) != 1:
        sys.exit(f"site/app.html must contain {MARK} exactly once")
    data = (BUILD / "outlook.json").read_text()
    # inline <script> data: a literal "</script" inside a string would end the block
    html = tpl.replace(MARK, data.replace("</", "<\\/"))
    out = BUILD / "outlook.html"
    out.write_text(html)
    print(f"{out}: {len(html) / 1e6:.2f} MB (plaintext, local preview only)")

    if args.publish:
        pw = os.environ.get("PAGE_PASSWORD")
        if not pw:
            sys.exit("set PAGE_PASSWORD")
        dst = ROOT / "docs" / "outlook"
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "page.enc").write_text(encrypt(html.encode(), pw))
        shutil.copyfile(ROOT / "site" / "gate.html", dst / "index.html")
        print(f"{dst}/page.enc: {(dst / 'page.enc').stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
