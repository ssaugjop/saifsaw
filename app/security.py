"""
SAWHost - Security primitives
=============================
مكتبة قياسية فقط (hashlib/hmac/secrets) كي لا تضيف أي اعتمادية جديدة.

  - seal()/unseal(): تشفير توكنات GitHub بنمط encrypt-then-MAC
        keystream = SHA256(key_enc || nonce || counter)  (وضع CTR)
        tag       = HMAC-SHA256(key_mac, version || nonce || ciphertext)
    المفتاحان مشتقّان من SECRET_KEY بفصل نطاقات (domain separation).
  - csrf_token(): رمز مرتبط بالجلسة (HMAC)، يُسلَّم فقط لتنقّلات المتصفح الحقيقية،
    فلا يستطيع موقع مستضاف (نفس الأصل) قراءته بـ fetch.
  - safe_filename(): تعقيم اسم الملف في ترويسة Content-Disposition.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from app.config import get_cfg


def _derive(label: str) -> bytes:
    return hashlib.sha256(f"sawhost:{label}:".encode() + get_cfg().secret_key.encode()).digest()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + ctr.to_bytes(8, "big")).digest()
        ctr += 1
    return bytes(out[:n])


def seal(plaintext: str) -> str:
    """يشفّر نصاً ويوقّعه. النتيجة نص آمن للتخزين في قاعدة البيانات."""
    data = plaintext.encode()
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(_derive("enc"), nonce, len(data))))
    tag = hmac.new(_derive("mac"), b"\x01" + nonce + ct, hashlib.sha256).digest()
    return "v1." + _b64e(nonce + ct + tag)


def unseal(token: str | None) -> str | None:
    """يفكّ التشفير بعد التحقق من التوقيع. يرجع None عند أي تلاعب أو مفتاح مختلف."""
    if not token or not token.startswith("v1."):
        return None
    try:
        raw = _b64d(token[3:])
        nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
        good = hmac.new(_derive("mac"), b"\x01" + nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, good):
            return None
        return bytes(a ^ b for a, b in zip(ct, _stream(_derive("enc"), nonce, len(ct)))).decode()
    except Exception:
        return None


def csrf_token(session_cookie: str | None) -> str:
    """رمز CSRF مرتبط بالكوكي الحالي (أو 'anon' قبل تسجيل الدخول)."""
    msg = "csrf|" + (session_cookie or "anon")
    return _b64e(hmac.new(_derive("csrf"), msg.encode(), hashlib.sha256).digest())


def csrf_ok(session_cookie: str | None, given: str | None) -> bool:
    if not given:
        return False
    return hmac.compare_digest(csrf_token(session_cookie), given)


def edge_trusted(headers) -> bool:
    """الطلب جاء عبر الـ Worker/البروكسي الخاص بنا (يحمل السرّ المشترك EDGE_SECRET)؟ مقارنة بزمن ثابت."""
    secret = get_cfg().edge_secret
    got = headers.get("X-Saw-Edge", "")
    return bool(secret and got and hmac.compare_digest(secret.encode(), got.encode()))


def effective_host(req) -> str:
    """المضيف الذي كتبه الزائر فعلاً. خلف Worker يصل Host الخاص بالـ Space، والمضيف الأصلي يُمرَّر
    في X-Saw-Host، ولا يُصدَّق إلا مع السرّ المشترك (وإلا يستطيع أي شخص انتحال مضيف أي موقع)."""
    if edge_trusted(req.headers):
        h = req.headers.get("X-Saw-Host", "").split(":")[0].strip().lower()
        if h:
            return h
    return req.host.split(":")[0].lower()


_BAD_FN = re.compile(r'[^\w.\- ]+', re.UNICODE)


def safe_filename(name: str, default: str = "file") -> str:
    """يمنع حقن الترويسات (CR/LF والاقتباس) في Content-Disposition."""
    n = _BAD_FN.sub("_", (name or "").replace("\\", "/").split("/")[-1]).strip(" .")
    return (n or default)[:120]
