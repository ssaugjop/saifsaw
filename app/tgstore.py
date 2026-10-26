"""
SAWHost - التخزين الدائم في قناة تلجرام خاصة (app/tgstore.py)  — الإصدار 2
==========================================================================
يحلّ مشكلة «القرص يُمسح عند كل إعادة تشغيل» (GitHub Actions / HuggingFace) بلا أي خدمة خارجية. كل البيانات ملفات
مشفّرة AES-256-GCM داخل قناة تلجرام خاصة يملكها الأدمن.

البنية
------
  • قاعدة البيانات: لقطة SQLite كاملة مضغوطة ومشفّرة (مقسّمة لأجزاء ≤ 15MB).
  • المواقع (v2): أرشيف مستقل لكل موقع، تُجمَّع الأرشيفات في «حزم» ≤ 15MB (انظر sitepacks.py). حفظ تزايدي (يُرفع المتغيّر
    فقط)، تنظيف تلقائي للحزم الميتة، واستعادة كسولة للمواقع الكبيرة — فلا سقف عملياً على عدد المواقع ولا يتأخر الإقلاع.
  • المانيفست (JSON مضغوط ومشفّر) يسرد القاعدة والمواقع والحزم ويُثبَّت في القناة. عند الإقلاع:
    getChat(channel).pinned_message ← manifest ← الأجزاء ← استعادة.
  • مؤشر القناة يُحفظ كرسالة مثبّتة في محادثة الأدمن مع البوت، أو في Secret اسمه STORAGE_CHANNEL_ID.
  • جيلان دائماً (الحالي + السابق)؛ ما قبلهما يُحذف تلقائياً.

حدود تيليجرام المراعاة
----------------------
  • رفع ≤ 50MB وتنزيل البوت ≤ 20MB للملف → كل جزء ≤ 15MB قبل التشفير (15MB + 28 بايت بعده).
  • رسالة/ثانية لكل محادثة → فاصل SEND_GAP بين الرسائل + احترام retry_after. تجميع الأرشيفات الصغيرة يقلّل الرسائل بمئات المرات.
  • التحقق من حجم المستند المرفوع، ثم قراءة المانيفست المثبّت بعد كل حفظ للتأكد أنه سليم وقابل للفك.

الأمان من فقدان البيانات
------------------------
  • فشلت الاستعادة (مفتاح مختلف / ملف تالف) → وضع «قراءة فقط»: لا حفظ ولا حذف. لا تُستبدل لقطة سليمة بقاعدة فارغة أبداً.
  • موقع تالف أو لم يُستعَد بعد (بارد) لا يُحفظ فوق نسخته السليمة أبداً.
  • الرفع ذرّي: الحزم أولاً ثم المانيفست ثم التثبيت. أي فشل يزيل ما رُفع فقط وتبقى اللقطة السابقة سليمة ومثبّتة.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from app import sitepacks as SP
from app.config import get_cfg

log = logging.getLogger("sawhost.tgstore")

# ── ثوابت قابلة للضبط عبر البيئة ───────────────────────────────────────────────
PART_SIZE = 15 * 1024 * 1024                                   # حجم الجزء قبل التشفير (حدّ التحميل 20MB)
POINTER_PREFIX = "SAWSTORE:v1:"
_POINTER_RE = re.compile(r"SAWSTORE:v1:(-?\d+)")
MANIFEST_NAME = "sawhost-manifest.bin"
_MANIFEST_AAD = b"sawhost-manifest-v1"


def _env_int(k: str, d: int) -> int:
    try:
        return int(os.getenv(k, str(d)))
    except ValueError:
        return d


DB_INTERVAL = _env_int("TGSTORE_DB_INTERVAL", 300)             # أقل فاصل بين لقطتين للقاعدة (ث)
SITES_INTERVAL = _env_int("TGSTORE_SITES_INTERVAL", 1800)      # أقل فاصل بين لقطتين للمواقع (ث)
EAGER_SITE_MB = _env_int("TGSTORE_EAGER_SITE_MB", 25)           # موقع أصغر من هذا يُستعاد فوراً عند الإقلاع
EAGER_TOTAL_MB = _env_int("TGSTORE_EAGER_TOTAL_MB", 400)        # ميزانية الاستعادة الفورية؛ الباقي يُستعاد عند أول طلب
EAGER_ALL = os.getenv("TGSTORE_EAGER_ALL", "") == "1"
EAGER_SECONDS = _env_int("TGSTORE_EAGER_SECONDS", 240)
TOTAL_WARN_BYTES = _env_int("TGSTORE_TOTAL_WARN_GB", 20) * 1024 ** 3
TICK = 30                                                      # دورة فحص التغييرات (ث)


def ci(msg: str) -> None:
    """سطر آمن للعرض في لوج GitHub العام (لا يحتوي بيانات مستخدمين أبداً)."""
    print(f"[storage] {msg}", flush=True)


# ── الحالة ────────────────────────────────────────────────────────────────────
class _State:
    def __init__(self) -> None:
        self.channel_id: int | None = None
        self.channel_title: str = ""
        self.manifest: dict | None = None
        self.manifest_mid: int | None = None
        self.read_only: bool = False
        self.read_only_reason: str = ""
        self.last_error: str = ""
        self.last_save_at: datetime | None = None
        self.last_db_ver: int | None = None
        self.last_db_sha: str | None = None
        self.last_sites_sig: str | None = None
        self.last_db_save: float = -1e9
        self.last_sites_save: float = -1e9
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.notified: dict[str, float] = {}
        self.bot = None
        self.cold: set[str] = set()          # مواقع في القناة لم تُستعَد بعد (تُستعاد عند أول طلب)
        self.bad: dict[str, str] = {}        # مواقع تالفة: لا تُحفظ فوق نسختها السليمة
        self.site_locks: dict[str, asyncio.Lock] = {}
        self.stats: dict = {}


S = _State()
_KEY: bytes | None = None
_mon: sqlite3.Connection | None = None


# ══════════════════════════════════════════════════════════════════════════════
#  التشفير والتقسيم (دوال نقية — قابلة للاختبار بدون تيليجرام)
# ══════════════════════════════════════════════════════════════════════════════
def _key() -> bytes:
    global _KEY
    if _KEY is None:
        _KEY = hashlib.pbkdf2_hmac("sha256", get_cfg().secret_key.encode(), b"sawhost-tgstore-v1", 200_000, 32)
    return _KEY


def _enc(plain: bytes, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, plain, aad)


def _dec(blob: bytes, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], aad)


def _pack_manifest(man: dict) -> bytes:
    raw = json.dumps(man, separators=(",", ":"), ensure_ascii=False).encode()
    return _enc(gzip.compress(raw, 6, mtime=0), _MANIFEST_AAD)


def _unpack_manifest(blob: bytes) -> dict:
    raw = _dec(blob, _MANIFEST_AAD)
    if raw[:2] == b"\x1f\x8b":                                   # v2 مضغوط؛ v1 كان JSON خاماً
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _seal_parts(stream: str, gen: int, raw: bytes) -> list[bytes]:
    chunks = [raw[i:i + PART_SIZE] for i in range(0, len(raw), PART_SIZE)] or [b""]
    total = len(chunks)
    return [_enc(c, f"{stream}|{gen}|{i}|{total}".encode()) for i, c in enumerate(chunks)]


def _open_parts(stream: str, gen: int, blobs: list[bytes]) -> bytes:
    total = len(blobs)
    return b"".join(_dec(b, f"{stream}|{gen}|{i}|{total}".encode()) for i, b in enumerate(blobs))


# ══════════════════════════════════════════════════════════════════════════════
#  حزم/فك ملفات التطبيق
# ══════════════════════════════════════════════════════════════════════════════
def _db_path() -> Path:
    p = get_cfg().sqlite_path
    if p.startswith("/data") and not os.path.exists("/data"):   # نفس منطق Cfg.async_dsn
        p = "./sawhost_dev.db"
    return Path(p)


def _sites_root() -> Path:
    return Path(get_cfg().sites_root)


def _has_files(root: Path) -> bool:
    try:
        return root.is_dir() and any(root.iterdir())
    except OSError:
        return False


def _db_version() -> int | None:
    """PRAGMA data_version يتغيّر عندما تُعدِّل اتصالات أخرى القاعدة → كشف تغيير رخيص بلا قراءة الملف."""
    global _mon
    p = _db_path()
    if not p.exists():
        return None
    if _mon is None:
        _mon = sqlite3.connect(str(p), check_same_thread=False)
    return _mon.execute("PRAGMA data_version").fetchone()[0]


def _reset_monitor() -> None:
    global _mon
    if _mon is not None:
        try:
            _mon.close()
        except Exception:
            pass
        _mon = None


def _pack_db() -> bytes | None:
    """نسخة متسقة من القاعدة الحيّة (واجهة backup الرسمية تعمل مع WAL) ثم gzip بلا طابع زمني (بصمة ثابتة)."""
    p = _db_path()
    if not p.exists():
        return None
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        last: Exception | None = None
        for _ in range(4):
            try:
                src = sqlite3.connect(str(p))
                dst = sqlite3.connect(tmp)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
                last = None
                break
            except sqlite3.OperationalError as e:
                last = e
                time.sleep(1.0)
        if last:
            raise last
        data = Path(tmp).read_bytes()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return gzip.compress(data, 6, mtime=0)


def _unpack_db(gz: bytes) -> None:
    data = gzip.decompress(gz)
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".restore")
    tmp.write_bytes(data)
    con = sqlite3.connect(str(tmp))
    try:
        ok = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    if ok != "ok":
        tmp.unlink(missing_ok=True)
        raise ValueError(f"فحص سلامة القاعدة المستعادة فشل: {ok}")
    for suf in ("-wal", "-shm"):
        Path(str(p) + suf).unlink(missing_ok=True)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


# مجلدات تُعاد صناعتها عند تشغيل التطبيق (مكتبات وكاش وبناء): لا تُحفظ في لقطات تيليجرام
# كي لا تتضخم اللقطة ولا تُرفع من جديد كل دورة بسبب تغيّر ملفات الكاش.
_SNAP_SKIP_DIRS = frozenset({"node_modules", ".npm", ".cache", ".local", "__pycache__", ".venv", "venv",
                             ".git", ".gradle", ".composer", "vendor_cache"})


def _sites_sig() -> tuple[str, int]:
    root = _sites_root()
    if not root.is_dir():
        return "", 0
    h = hashlib.sha1()
    total = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in _SNAP_SKIP_DIRS)
        for fn in sorted(fns):
            fp = os.path.join(dp, fn)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            total += st.st_size
            h.update(f"{os.path.relpath(fp, root)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest(), total


def _pack_sites() -> bytes | None:
    root = _sites_root()
    if not _has_files(root):
        return None
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=3) as tar:
        def _flt(ti: tarfile.TarInfo):
            parts = ti.name.split("/")
            return None if any(x in _SNAP_SKIP_DIRS for x in parts) else ti
        for entry in sorted(root.iterdir()):
            tar.add(str(entry), arcname="sites/" + entry.name, filter=_flt)
    return buf.getvalue()


def _unpack_sites(gz: bytes) -> int:
    """فك آمن: ملفات ومجلدات عادية فقط، وأي مسار يخرج عن الجذر يُتجاهل (Path Traversal)."""
    root = _sites_root()
    root.mkdir(parents=True, exist_ok=True)
    base = root.resolve()
    n = 0
    with tarfile.open(fileobj=io.BytesIO(gz), mode="r:gz") as tar:
        for m in tar:
            if not m.name.startswith("sites/"):
                continue
            rel = m.name[len("sites/"):]
            if not rel:
                continue
            target = (base / rel).resolve()
            if target != base and base not in target.parents:
                continue
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(m)
                if src is None:
                    continue
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                try:
                    os.chmod(target, m.mode & 0o777)
                    os.utime(target, (m.mtime, m.mtime))
                except OSError:
                    pass
                n += 1
    return n


# ══════════════════════════════════════════════════════════════════════════════
#  تيليجرام I/O
# ══════════════════════════════════════════════════════════════════════════════
def _mk_file(blob: bytes, name: str):
    from aiogram.types import BufferedInputFile
    return BufferedInputFile(blob, filename=name)


async def _retry(fn, tries: int = 5):
    """إعادة محاولة مع احترام retry_after؛ أخطاء الصلاحيات/الطلب الخاطئ لا تُعاد."""
    try:
        from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
        fatal: tuple = (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError)
    except Exception:  # pragma: no cover
        fatal = ()
    delay = 2.0
    for i in range(tries):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if fatal and isinstance(e, fatal):
                raise
            if i == tries - 1:
                raise
            ra = getattr(e, "retry_after", None)
            await asyncio.sleep((ra + 1) if ra else delay)
            delay = min(delay * 2, 30.0)


async def _send_blob(bot, chat_id: int, filename: str, blob: bytes, caption: str | None = None) -> tuple[int, str]:
    last: Exception | None = None
    for _ in range(3):
        async def go():
            return await bot.send_document(chat_id, _mk_file(blob, filename), caption=caption,
                                           disable_notification=True, request_timeout=600)
        m = await _retry(go)
        sz = getattr(m.document, "file_size", None)
        if sz is None or sz == len(blob):
            return m.message_id, m.document.file_id
        last = IOError(f"حجم المستند المرفوع ({sz}) لا يطابق المُرسَل ({len(blob)})")   # تحقق صارم
        try:
            await bot.delete_message(chat_id, m.message_id)
        except Exception:
            pass
        await asyncio.sleep(2)
    raise last or IOError("فشل رفع الملف")


async def _fetch_blob(bot, file_id: str) -> bytes:
    async def go():
        fi = await bot.get_file(file_id, request_timeout=60)
        buf = await bot.download_file(fi.file_path, timeout=600)
        return buf.read()
    return await _retry(go)


async def _delete_msgs(bot, chat_id: int, mids: list[int]) -> None:
    for mid in mids:
        if not mid:
            continue
        try:
            await bot.delete_message(chat_id, mid)
        except Exception as e:  # تنظيف اختياري
            log.debug("delete %s failed: %s", mid, e)


async def _notify(bot, text: str, key: str, every: int = 1800) -> None:
    """تنبيه الأدمن في الخاص (مع كبح التكرار)."""
    now = time.monotonic()
    if now - S.notified.get(key, -1e9) < every:
        return
    S.notified[key] = now
    try:
        await bot.send_message(get_cfg().admin_id, text, parse_mode="HTML")
    except Exception as e:
        log.warning("notify admin failed: %s", e)


class TgIO:
    """تنفيذ SP.PackIO فوق قناة تيليجرام."""

    def __init__(self, bot, channel_id: int):
        self.bot, self.cid = bot, channel_id

    async def put(self, pack_id: str, blob: bytes) -> dict:
        mid, fid = await _send_blob(self.bot, self.cid, f"pack-{pack_id}.bin", blob)
        return {"mid": mid, "fid": fid}

    async def get(self, rec: dict) -> bytes:
        return await _fetch_blob(self.bot, rec["fid"])

    async def drop(self, mids: list[int]) -> None:
        await _delete_msgs(self.bot, self.cid, mids)


def _known_site_ids() -> set[str] | None:
    """معرّفات المواقع الموجودة فعلاً في القاعدة (لإسقاط المحذوفة من الفهرس). None = تعذّر الجزم → لا نحذف شيئاً."""
    p = _db_path()
    if not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
        try:
            try:
                rows = con.execute("SELECT id FROM sites WHERE COALESCE(is_deleted, 0) = 0")
            except sqlite3.Error:
                rows = con.execute("SELECT id FROM sites")
            return {str(r[0]) for r in rows}
        finally:
            con.close()
    except Exception:
        return None


def _present_with_files(name: str) -> bool:
    d = _sites_root() / name
    try:
        return d.is_dir() and any(d.iterdir())
    except OSError:
        return False


async def ensure_site(name: str) -> bool:
    """يُستدعى قبل لمس ملفات موقع. إن كان «بارداً» (موجوداً في القناة فقط) يُستعاد الآن. True = جاهز."""
    name = str(name)
    if name not in S.cold:
        return name not in S.bad
    if not S.bot or not S.channel_id or not S.manifest:
        return True
    lock = S.site_locks.setdefault(name, asyncio.Lock())
    async with lock:
        if name not in S.cold:
            return name not in S.bad
        ent = (S.manifest.get("sites") or {}).get(name)
        if not ent:
            S.cold.discard(name)
            return True
        try:
            reader = SP.PackReader(TgIO(S.bot, S.channel_id), _dec, S.manifest.get("packs") or {})
            await SP.restore_one(_sites_root(), name, ent, reader)
            await SP.refresh_sigs(_sites_root(), S.manifest["sites"], [name])
            S.cold.discard(name)
            ci(f"استُعيد موقع بارد ({_fmt_size(ent['size'])}).")
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("lazy restore failed for %s: %s", name, e)
            if isinstance(e, SP.IntegrityError) or type(e).__name__ == "InvalidTag":
                S.bad[name] = f"{type(e).__name__}: {str(e)[:160]}"          # تالف فعلاً
                S.cold.discard(name)
                await _notify(S.bot, f"🔴 <b>موقع تالف في القناة</b>\n<code>{name}</code>\n{S.bad[name]}", f"bad:{name}", 3600)
            return False                                                      # عابر: يبقى بارداً فيُعاد المحاولة


# ── مؤشر القناة ───────────────────────────────────────────────────────────────
async def _resolve_channel(bot) -> int | None:
    env = os.getenv("STORAGE_CHANNEL_ID", "").strip()
    if env.lstrip("-").isdigit():
        return int(env)
    try:
        chat = await _retry(lambda: bot.get_chat(get_cfg().admin_id), tries=3)
    except Exception as e:
        log.warning("pointer lookup failed: %s", e)
        return None
    pm = getattr(chat, "pinned_message", None)
    text = (getattr(pm, "text", None) or "") if pm else ""
    m = _POINTER_RE.search(text)
    return int(m.group(1)) if m else None


async def _write_pointer(bot, channel_id: int) -> None:
    admin = get_cfg().admin_id
    try:
        await bot.unpin_all_chat_messages(admin)
    except Exception:
        pass
    m = await bot.send_message(
        admin,
        "💾 <b>مؤشر قناة تخزين SAWHost</b>\n"
        "لا تحذف هذه الرسالة ولا تفكّ تثبيتها — بها يجد البوت بياناته بعد كل إعادة تشغيل.\n"
        f"<code>{POINTER_PREFIX}{channel_id}</code>",
        parse_mode="HTML", disable_notification=True,
    )
    await bot.pin_chat_message(admin, m.message_id, disable_notification=True)


async def _read_manifest(bot, channel: int) -> tuple[dict | None, int | None, str]:
    """يرجع (manifest, message_id, عنوان القناة). manifest=None إن لم توجد لقطة بعد."""
    chat = await _retry(lambda: bot.get_chat(channel), tries=4)
    title = getattr(chat, "title", "") or ""
    pm = getattr(chat, "pinned_message", None)
    doc = getattr(pm, "document", None) if pm else None
    if not doc or getattr(doc, "file_name", "") != MANIFEST_NAME:
        return None, None, title
    blob = await _fetch_blob(bot, doc.file_id)
    man = _unpack_manifest(blob)
    return man, pm.message_id, title


# ══════════════════════════════════════════════════════════════════════════════
#  الإقلاع: استعادة
# ══════════════════════════════════════════════════════════════════════════════
def _fail_read_only(reason: str) -> None:
    S.read_only = True
    S.read_only_reason = reason
    S.last_error = reason
    ci(f"READ-ONLY: {reason}")
    log.error("tgstore read-only: %s", reason)


async def boot(bot) -> None:
    """يُستدعى قبل init_db. يستعيد القاعدة/المواقع من القناة إن كانت مفقودة محلياً."""
    cfg = get_cfg()
    try:
        S.channel_id = await _resolve_channel(bot)
    except Exception as e:
        log.warning("resolve channel failed: %s", e)
        S.channel_id = None
    if not S.channel_id:
        ci("لا توجد قناة تخزين مرتبطة (اربطها من /admin ← التخزين السحابي).")
        return

    force = os.getenv("TGSTORE_FORCE_RESTORE", "") == "1"
    need_db = cfg.is_sqlite and (force or not _db_path().exists() or _db_path().stat().st_size == 0)
    need_sites = force or not _has_files(_sites_root())

    man = mid = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            man, mid, S.channel_title = await _read_manifest(bot, S.channel_id)
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if type(e).__name__ == "InvalidTag":
                break
            await asyncio.sleep(5 * (attempt + 1))
    if last_exc is not None:
        why = ("تعذّر فك التشفير — SECRET_KEY مختلف عن الذي حُفظت به اللقطة أو الملف تالف"
               if type(last_exc).__name__ == "InvalidTag" else f"تعذّر قراءة القناة: {type(last_exc).__name__}")
        _fail_read_only(why)
        await _notify(bot, f"🔴 <b>التخزين السحابي في وضع القراءة فقط</b>\n{why}\nلن يُحفظ أو يُحذف شيء حتى تُحل المشكلة.", "boot")
        return

    S.manifest, S.manifest_mid = man, mid
    if not man:
        ci("القناة مرتبطة ولا توجد لقطة بعد (أول تشغيل).")
        S.last_db_ver = None          # بعد init_db سيظهر تغيير → تُحفظ أول لقطة تلقائياً
        return

    S.bot = bot
    try:
        if need_db and man.get("db"):
            raw = await _restore_stream(bot, "db", man["db"])
            await asyncio.to_thread(_unpack_db, raw)
            ci(f"تمت استعادة قاعدة البيانات ({man['db']['size'] // 1024} KB).")
        if int(man.get("v", 1)) >= 2:
            await _boot_sites_v2(bot, man)
        elif need_sites and man.get("sites"):                        # لقطة قديمة (v1): ملف tar واحد لكل المواقع
            raw = await _restore_stream(bot, "sites", man["sites"])
            n = await asyncio.to_thread(_unpack_sites, raw)
            ci(f"تمت استعادة ملفات المواقع من لقطة قديمة ({n} ملف) — ستُرقّى للنظام الجديد عند أول حفظ.")
    except Exception as e:
        why = ("تعذّر فك التشفير — SECRET_KEY مختلف" if type(e).__name__ == "InvalidTag"
               else f"فشلت الاستعادة: {type(e).__name__}: {e}")
        _fail_read_only(why[:300])
        await _notify(bot, f"🔴 <b>فشلت استعادة البيانات من القناة</b>\n{why[:300]}\nالحفظ التلقائي متوقف لحماية اللقطة السليمة.", "restore")
        return

    S.last_db_sha = (man.get("db") or {}).get("sha")
    S.last_sites_sig = (man.get("sites") or {}).get("sig") if int(man.get("v", 1)) < 2 else None
    S.last_db_ver = await asyncio.to_thread(_db_version)


async def _boot_sites_v2(bot, man: dict) -> None:
    """استعادة المواقع: الصغيرة فوراً (بميزانية)، والكبيرة «باردة» تُستعاد عند أول طلب."""
    sites, packs = man.get("sites") or {}, man.get("packs") or {}
    S.cold, S.bad = set(), {}
    if not sites:
        return
    missing = {n: e for n, e in sites.items() if not _present_with_files(n)}
    if not missing:
        ci(f"مواقع القناة: {len(sites)} — كلها موجودة محلياً.")
        return
    eager, cold = SP.split_eager_cold(missing, site_mb=EAGER_SITE_MB, total_mb=EAGER_TOTAL_MB, everything=EAGER_ALL)
    restored, deferred, bad = await SP.restore_sites(root=_sites_root(), sites=sites, packs=packs, io_=TgIO(bot, S.channel_id),
                                                      dec=_dec, eager=eager, time_budget=EAGER_SECONDS)
    await SP.refresh_sigs(_sites_root(), sites, restored)
    S.cold = set(cold) | set(deferred)
    S.bad = bad
    ci(f"المواقع: استُعيد {len(restored)} فوراً · {len(S.cold)} بارد (يُستعاد عند أول طلب) · تالف {len(bad)} · المجموع {len(sites)}.")
    if bad:
        await _notify(bot, f"🔴 <b>{len(bad)} موقع تالف في القناة</b> (لن يُكتب فوق نسخته السليمة):\n"
                           + "\n".join(f"<code>{n[:8]}…</code> {r[:90]}" for n, r in list(bad.items())[:8]), "bad_sites", 3600)


async def _restore_stream(bot, stream: str, ent: dict) -> bytes:
    blobs = [await _fetch_blob(bot, p["fid"]) for p in ent["parts"]]
    return await asyncio.to_thread(_open_parts, stream, ent["gen"], blobs)


# ══════════════════════════════════════════════════════════════════════════════
#  الحفظ
# ══════════════════════════════════════════════════════════════════════════════
def _active() -> bool:
    return bool(S.channel_id) and not S.read_only


async def _upload_stream(bot, cid: int, stream: str, gen: int, raw: bytes, uploaded: list[int]) -> dict:
    blobs = await asyncio.to_thread(_seal_parts, stream, gen, raw)
    parts = []
    for i, b in enumerate(blobs):
        mid, fid = await _send_blob(bot, cid, f"{stream}-{gen}-{i + 1:03d}of{len(blobs):03d}.bin", b)
        uploaded.append(mid)
        parts.append({"mid": mid, "fid": fid})
    return {"gen": gen, "size": len(raw), "parts": parts}


def _fmt_size(n: int) -> str:
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{n / 1024:.0f}KB"


async def save(bot, *, db: bool = True, sites: bool | None = None, reason: str = "manual") -> tuple[bool, str]:
    """
    يحفظ لقطة جديدة إن تغيّر شيء. sites=None → تلقائي حسب بصمة الملفات، True → افحص دائماً، False → تجاهل.
    يرجع (نجاح, رسالة للعرض).
    """
    if not S.channel_id:
        return False, "لا توجد قناة تخزين مرتبطة."
    if S.read_only:
        return False, f"الحفظ متوقف (قراءة فقط): {S.read_only_reason}"
    async with S.lock:
        try:
            return await _save_locked(bot, db, sites, reason)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            S.last_error = f"{type(e).__name__}: {e}"[:300]
            log.warning("snapshot failed (%s): %s", reason, S.last_error)
            ci(f"فشل الحفظ ({reason}): {type(e).__name__}")
            await _notify(bot, f"⚠️ <b>فشل حفظ لقطة التخزين</b>\n{S.last_error}", "save")
            return False, f"فشل الحفظ: {S.last_error}"


async def _save_locked(bot, do_db: bool, do_sites: bool | None, reason: str) -> tuple[bool, str]:
    cfg = get_cfg()
    cid = S.channel_id
    old = S.manifest or {}
    gen = max(int(time.time()), int(old.get("gen", 0)) + 1)   # رقم جيل فريد ومتصاعد دائماً
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new: dict = {"v": 1, "gen": gen, "ts": now_iso, "prev_manifest_id": S.manifest_mid}
    uploaded: list[int] = []
    del_mids: list[int] = []
    changed = False
    ver = None
    sig = None

    try:
        # ── القاعدة ──
        old_db = old.get("db")
        new["db"] = old_db
        if do_db and cfg.is_sqlite:
            ver = await asyncio.to_thread(_db_version)
            raw = await asyncio.to_thread(_pack_db)
            if raw:
                sha = hashlib.sha256(raw).hexdigest()
                if sha != (old_db or {}).get("sha"):
                    ent = await _upload_stream(bot, cid, "db", gen, raw, uploaded)
                    ent["sha"] = sha
                    ent["prev_mids"] = [p["mid"] for p in (old_db or {}).get("parts", [])]
                    del_mids += (old_db or {}).get("prev_mids", [])
                    new["db"] = ent
                    changed = True

        # ── المواقع (v2: حفظ تزايدي بحزم) ──
        legacy = old.get("sites") if int(old.get("v", 1)) < 2 else None          # لقطة قديمة تحتاج ترقية
        old_sites = {} if legacy is not None else (old.get("sites") or {})
        old_packs = {} if legacy is not None else (old.get("packs") or {})
        new["sites"], new["packs"], new["trash"] = old_sites, old_packs, []
        if legacy is not None:
            new["v"] = 1                                                          # نبقى على v1 حتى ننجح بالترقية
            new["sites"] = legacy
        if do_sites is not False and not (legacy is not None and not _has_files(_sites_root())):
            sites, packs, trash, st = await SP.save_sites(
                root=_sites_root(), old_sites=old_sites, old_packs=old_packs, io_=TgIO(bot, cid), enc=_enc, dec=_dec,
                gen=gen, known_ids=await asyncio.to_thread(_known_site_ids), skip_names=set(S.cold) | set(S.bad), uploaded=uploaded)
            sig, _tot = await asyncio.to_thread(_sites_sig)
            moved = bool(st["changed"] or st["gc"] or st["removed"] or trash or st["packs_uploaded"])
            if moved or legacy is not None:
                new["v"] = 2
                new["sites"], new["packs"] = sites, packs
                legacy_mids = ([p["mid"] for p in legacy.get("parts", [])] + list(legacy.get("prev_mids", []))) if legacy else []
                new["trash"] = list(trash) + legacy_mids
                changed = True
                S.stats = st
            if st["too_big"]:
                await _notify(bot, "⚠️ <b>مواقع أكبر من حدّ التخزين السحابي</b> (لم تُحفظ):\n"
                              + "\n".join(f"<code>{n[:8]}…</code> {r}" for n, r in st["too_big"][:6]), "sites_big", 6 * 3600)
        total_bytes = sum(e["size"] for e in (new.get("sites") or {}).values()) if int(new.get("v", 1)) >= 2 else 0
        if total_bytes > TOTAL_WARN_BYTES:
            await _notify(bot, f"⚠️ حجم أرشيف المواقع في القناة ({_fmt_size(total_bytes)}) تجاوز {_fmt_size(TOTAL_WARN_BYTES)}. "
                               "تيليجرام لا يحدّ القناة لكن الاستعادة ستطول؛ فكّر بتقليل المواقع الكبيرة.", "total_big", 24 * 3600)

        if not changed:
            if ver is not None:
                S.last_db_ver = ver
                S.last_db_save = time.monotonic()
            if sig is not None:
                S.last_sites_sig = sig
                S.last_sites_save = time.monotonic()
            return True, "لا توجد تغييرات جديدة — اللقطة الحالية محدّثة."

        # ── manifest ثم تثبيته ──
        blob = await asyncio.to_thread(_pack_manifest, new)
        if len(blob) > 18 * 1024 * 1024:
            raise ValueError(f"المانيفست ({_fmt_size(len(blob))}) تجاوز حدّ تنزيل تيليجرام (20MB) — عدد المواقع كبير جداً")
        n_sites = len(new.get("sites") or {}) if int(new.get("v", 1)) >= 2 else 0
        caption = (f"💾 SAWHost snapshot · {now_iso}\n"
                   f"db: {_fmt_size((new.get('db') or {}).get('size', 0))} · "
                   f"sites: {n_sites} · packs: {len(new.get('packs') or {})} · {reason}")
        mid, _fid = await _send_blob(bot, cid, MANIFEST_NAME, blob, caption)
        uploaded.append(mid)
        await _retry(lambda: bot.pin_chat_message(cid, mid, disable_notification=True))
    except BaseException:
        # فشل في منتصف الرفع: نزيل ما رُفع للتوّ فقط (اللقطة القديمة سليمة ومثبّتة).
        await _delete_msgs(bot, cid, uploaded)
        raise

    # ── نجاح: حدّث الحالة ثم نظّف ما قبل الجيل السابق ──
    S.manifest, S.manifest_mid = new, mid
    S.last_save_at = datetime.now(timezone.utc)
    S.last_error = ""
    if ver is not None:
        S.last_db_ver = ver
        S.last_db_save = time.monotonic()
        S.last_db_sha = (new.get("db") or {}).get("sha")
    if sig is not None:
        S.last_sites_sig = sig
        S.last_sites_save = time.monotonic()
    pm_old = old.get("prev_manifest_id")
    await _delete_msgs(bot, cid, del_mids + list(old.get("trash", [])) + ([pm_old] if pm_old else []))
    await _verify_pinned(bot, cid, mid, gen)
    v2 = int(new.get("v", 1)) >= 2
    sites_txt = (f"{len(new.get('sites') or {})} موقع · {_fmt_size(sum(e['size'] for e in (new.get('sites') or {}).values()))}"
                 if v2 else _fmt_size((new.get("sites") or {}).get("size", 0)))
    ci(f"تم حفظ لقطة جديدة ({reason}) — db {_fmt_size((new.get('db') or {}).get('size', 0))}, sites {sites_txt}")
    return True, f"تم الحفظ ✔ — القاعدة {_fmt_size((new.get('db') or {}).get('size', 0))} · المواقع {sites_txt}"


async def _verify_pinned(bot, cid: int, mid: int, gen: int) -> None:
    """تحقق بعد الحفظ: المانيفست المثبّت في القناة هو الجديد وقابل للقراءة والفك. فشله تنبيه فقط (لا حذف لأي شيء)."""
    try:
        man, pmid, _t = await _read_manifest(bot, cid)
        if not man or pmid != mid or int(man.get("gen", -1)) != gen:
            raise IOError("المانيفست المثبّت ليس الأحدث")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        S.last_error = f"تحقق ما بعد الحفظ: {type(e).__name__}: {e}"[:300]
        log.warning("post-save verification failed: %s", S.last_error)
        await _notify(bot, f"⚠️ <b>تحقق ما بعد الحفظ لم ينجح</b>\n{S.last_error}\nاللقطة السابقة ما زالت محفوظة.", "verify", 3600)


# ══════════════════════════════════════════════════════════════════════════════
#  الحفظ التلقائي + الإيقاف
# ══════════════════════════════════════════════════════════════════════════════
async def _maybe_save(bot) -> None:
    if not _active():
        return
    cfg = get_cfg()
    now = time.monotonic()
    do_db = False
    if cfg.is_sqlite and now - S.last_db_save >= DB_INTERVAL:
        do_db = (await asyncio.to_thread(_db_version)) != S.last_db_ver
    do_sites = False
    if now - S.last_sites_save >= SITES_INTERVAL:
        sig, total = await asyncio.to_thread(_sites_sig)
        do_sites = total > 0 and sig != S.last_sites_sig
    if do_db or do_sites:
        await save(bot, db=do_db, sites=do_sites, reason="auto")


async def _loop(bot) -> None:
    while True:
        try:
            await asyncio.sleep(TICK)
            await _maybe_save(bot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("autosave loop error: %s", e)


def start(bot) -> None:
    """يبدأ مهمة الحفظ التلقائي (بعد init_db)."""
    if S.task and not S.task.done():
        return
    S.task = asyncio.create_task(_loop(bot), name="tgstore-autosave")


async def stop(bot, final_save: bool = True) -> None:
    """إيقاف نظيف: يلغي المهمة ويحفظ لقطة أخيرة (مهلة 3 دقائق)."""
    if S.task:
        S.task.cancel()
        try:
            await S.task
        except BaseException:
            pass
        S.task = None
    if final_save and _active():
        try:
            ok, info = await asyncio.wait_for(save(bot, db=True, sites=None, reason="shutdown"), timeout=180)
            ci(f"لقطة الإيقاف: {'نجاح' if ok else 'فشل'} — {info}")
        except Exception as e:
            ci(f"لقطة الإيقاف فشلت: {type(e).__name__}")
    _reset_monitor()


# ══════════════════════════════════════════════════════════════════════════════
#  ربط/فصل القناة (تُستدعى من لوحة الأدمن)
# ══════════════════════════════════════════════════════════════════════════════
async def link_channel(bot, chat_id: int) -> tuple[bool, str]:
    """يتحقق من القناة والصلاحيات فعلياً (إرسال+تثبيت+حذف)، ثم يربطها ويكتب المؤشر."""
    try:
        chat = await bot.get_chat(chat_id)
    except Exception:
        return False, "تعذّر الوصول للقناة. تأكد أنني مضاف فيها كمشرف وأن المعرّف صحيح."
    if getattr(chat, "type", "") not in ("channel", "supergroup"):
        return False, "الوجهة ليست قناة. أنشئ قناة خاصة وأضفني مشرفاً فيها."
    step = "send"
    try:
        m = await bot.send_message(chat_id, "🔧 اختبار صلاحيات التخزين…", disable_notification=True)
        step = "pin"
        await bot.pin_chat_message(chat_id, m.message_id, disable_notification=True)
        step = "unpin"
        await bot.unpin_chat_message(chat_id, m.message_id)
        step = "delete"
        await bot.delete_message(chat_id, m.message_id)
    except Exception as e:
        # نُظهر الخطوة الفاشلة ونص الخطأ الحقيقي (مقتطع) بدل رسالة «الصلاحيات ناقصة» العمياء:
        # ValidationError مثلاً خطأ تحليل رد من المكتبة وليس نقص صلاحيات.
        detail = (str(e) or "").replace("\n", " ")[:220]
        kind = type(e).__name__
        if kind in ("TelegramForbiddenError", "TelegramBadRequest"):
            return False, ("الصلاحيات ناقصة. فعّل لي: نشر الرسائل + تعديل رسائل الآخرين (للتثبيت) + حذف الرسائل.\n"
                           f"({step}: {kind}: {detail})")
        return False, f"فشل فحص القناة عند الخطوة «{step}» — خطأ برمجي لا صلة له بالصلاحيات غالباً.\n({kind}: {detail})"

    # قناة فيها لقطة سابقة؟ اعتمدها (دون استعادة فوق البيانات الحيّة) بشرط أن يفكّ مفتاحنا تشفيرها.
    man = mid = None
    try:
        man, mid, _t = await _read_manifest(bot, chat_id)
    except Exception as e:
        if type(e).__name__ == "InvalidTag":
            return False, "في هذه القناة لقطة مشفّرة بمفتاح SECRET_KEY مختلف. استخدم قناة جديدة أو أعد المفتاح القديم."
        return False, f"تعذّرت قراءة القناة: {type(e).__name__}"

    try:
        await _write_pointer(bot, chat_id)
    except Exception as e:
        return False, f"تعذّر تثبيت مؤشر القناة في محادثتك مع البوت: {type(e).__name__}"

    S.channel_id = chat_id
    S.channel_title = getattr(chat, "title", "") or ""
    S.manifest, S.manifest_mid = man, mid
    S.read_only, S.read_only_reason, S.last_error = False, "", ""
    S.last_db_ver = None
    S.last_sites_sig = None
    S.last_db_save = S.last_sites_save = -1e9
    S.bot = bot
    S.cold, S.bad = set(), {}
    ci("تم ربط قناة التخزين.")
    return True, f"تم ربط القناة «{S.channel_title or chat_id}» بنجاح."


async def unlink(bot) -> None:
    S.channel_id = None
    S.channel_title = ""
    S.manifest, S.manifest_mid = None, None
    S.cold, S.bad = set(), {}
    try:
        await bot.unpin_all_chat_messages(get_cfg().admin_id)
    except Exception:
        pass
    ci("تم فصل قناة التخزين.")


def status_text() -> str:
    from html import escape
    if not S.channel_id:
        return ("💾 <b>التخزين السحابي (قناة تلجرام)</b>\n\n⚪ غير مرتبط.\n"
                "اربط قناة خاصة ليحفظ البوت كل بياناته فيها (مشفّرة) ويستعيدها تلقائياً بعد أي إعادة تشغيل.")
    if S.read_only:
        state = f"🔴 قراءة فقط — {escape(S.read_only_reason)}"
    elif S.last_error:
        state = f"🟠 يعمل (آخر خطأ: {escape(S.last_error)})"
    else:
        state = "🟢 يعمل"
    man = S.manifest or {}
    db_sz = _fmt_size((man.get("db") or {}).get("size", 0)) if man.get("db") else "—"
    if int(man.get("v", 1)) >= 2:
        ents = man.get("sites") or {}
        st_sz = (f"{len(ents)} موقع · {_fmt_size(sum(e['size'] for e in ents.values()))} · {len(man.get('packs') or {})} حزمة"
                 + (f" · ❄️ {len(S.cold)} بارد" if S.cold else "") + (f" · 🔴 {len(S.bad)} تالف" if S.bad else ""))
    else:
        st_sz = _fmt_size((man.get("sites") or {}).get("size", 0)) if man.get("sites") else "—"
    last = man.get("ts") or "لم تُحفظ لقطة بعد"
    return ("💾 <b>التخزين السحابي (قناة تلجرام)</b>\n\n"
            f"الحالة: {state}\n"
            f"القناة: {escape(S.channel_title) or '—'} <code>{S.channel_id}</code>\n"
            f"آخر لقطة: {escape(str(last))}\n"
            f"القاعدة: {db_sz} · المواقع: {st_sz}\n"
            f"الحفظ التلقائي: كل {DB_INTERVAL // 60} دقائق عند وجود تغيير + لقطة أخيرة عند الإيقاف.")
