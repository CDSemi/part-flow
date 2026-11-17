"""QR codes of authenticator app links (Phase 17, two-step sign-in).

``segno`` (pure Python, no dependencies) encodes the ``otpauth://`` link of
a new authenticator app; the browser draws the modules itself, so no image
format and no third-party script reaches the page. Error correction level
M (15 %) suits a code read from a screen.
"""

import segno


def rows(text: str) -> list[str]:
    """The QR code of ``text`` as rows of ``1`` (dark) and ``0`` (light), without the quiet zone."""
    code = segno.make(text, error="m", micro=False)
    return ["".join("1" if module else "0" for module in row) for row in code.matrix]
