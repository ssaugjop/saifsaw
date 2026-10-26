"""
SAWHost — محرك تخزين المواقع (app/sitepacks.py)
===============================================
يحلّ مشكلة «ألف موقع» دون أخطاء ودون تجاوز حدود تيليجرام. منطق نقي بلا اتصال: عمليات تيليجرام تُحقن عبر واجهة
PackIO، فيمكن اختباره كاملاً بمخزن وهمي.

التصميم
-------
  • لكل موقع أرشيف tar.gz مستقل (sites/<id>/…) ببصمة sha256. لا يُحفظ في الأرشيف: المجلدات المؤقتة (node_modules…)،
    الروابط الرمزية، الملفات الخاصة.
  • الأرشيفات الصغيرة تُجمَّع معاً في «حزم» حجمها ≤ PART_SIZE (15MB)، فيلزم رسالة واحدة لعشرات المواقع بدل رسالة لكل موقع
    (حدّ تيليجرام ≈ رسالة/ثانية لكل محادثة). والموقع الكبير يُقسَّم لحزم متتالية. كل حزمة تُشفَّر AES-256-GCM
    مع AAD يربطها بمعرّفها فلا يمكن تبديل حزمة بأخرى.
  • حفظ تزايدي: يُعاد رفع المواقع التي تغيّرت بصمتها فقط. الباقي يبقى حيث هو، والمانيفست يشير إليه.
  • تنظيف تلقائي: الحزم التي لم يعد يشير إليها شيء تُحذف بعد جيل، والحزم الفقيرة (أقل من نصفها حيّ) أو الصغيرة المتكاثرة تُدمج.
  • استعادة: فورية للمواقع الصغيرة، و«باردة» (عند أول طلب) للكبيرة، فلا يتأخر الإقلاع مهما كبر المجموع.
  • تحقق صارم: GCM لكل حزمة + sha256 لكل موقع + فك آمن (لا Path Traversal، لا روابط، حدود لحجم/عدد الملفات).
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable, Protocol


def _env_int(k: str, d: int) -> int:
    try:
        return int(os.getenv(k, str(d)))
    except ValueError:
        return d


PART_SIZE = _env_int("TGSTORE_PART_MB", 15) * 1024 * 1024        # قبل التشفير. حدّ تحميل البوت 20MB
MAX_SITE_BYTES = _env_int("TGSTORE_MAX_SITE_MB", 1024) * 1024 * 1024   # أقصى حجم أرشيف لموقع واحد
MAX_UNPACK_BYTES = _env_int("TGSTORE_MAX_UNPACK_MB", 4096) * 1024 * 1024
MAX_MEMBERS = _env_int("TGSTORE_MAX_FILES", 300_000)
SEND_GAP = float(os.getenv("TGSTORE_SEND_GAP", "1.1"))           # ثانية بين رسالتين لنفس القناة (حدّ تيليجرام)
GC_MAX_BYTES = _env_int("TGSTORE_GC_MB", 120) * 1024 * 1024       # أقصى حجم يُعاد تعبئته في حفظ واحد
SMALL_PACK = 1 * 1024 * 1024
SMALL_PACK_TRIGGER = 8                                             # عدد الحزم الصغيرة الذي يستدعي دمجها

SKIP_DIRS = frozenset({"node_modules", ".npm", ".cache", ".local", "__pycache__", ".venv", "venv",
                       ".git", ".gradle", ".composer", "vendor_cache"})


class PackIO(Protocol):
    async def put(self, pack_id: str, blob: bytes) -> dict: ...      # يرجع {"mid":..,"fid":..}
    async def get(self, rec: dict) -> bytes: ...
    async def drop(self, mids: list[int]) -> None: ...


Enc = Callable[[bytes, bytes], bytes]


class SiteTooBig(Exception):
    pass


class IntegrityError(Exception):
    pass


def pack_aad(pack_id: str, raw_len: int) -> bytes:
    return f"sawpack|{pack_id}|{raw_len}".encode()


# ══════════════════════════════════════════════════════════════════════════════
#  بصمة الموقع + بناء الأرشيف + فكّه
# ══════════════════════════════════════════════════════════════════════════════
def site_sig(site_dir: Path) -> tuple[str, int, int]:
    """(بصمة, مجموع بايتات الملفات, عدد الملفات) — رخيصة: لا قراءة لمحتوى الملفات."""
    h = hashlib.sha1()
    total = n = 0
    base = str(site_dir)
    for dp, dns, fns in os.walk(base):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not os.path.islink(os.path.join(dp, d)))
        rel_dir = os.path.relpath(dp, base)
        h.update(f"d|{rel_dir}\n".encode())
        for fn in sorted(fns):
            fp = os.path.join(dp, fn)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if not _is_reg(st.st_mode):
                continue
            total += st.st_size
            n += 1
            h.update(f"{os.path.join(rel_dir, fn)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest(), total, n


def _is_reg(mode: int) -> bool:
    import stat
    return stat.S_ISREG(mode)


def build_archive(site_dir: Path, name: str, out_path: Path, max_bytes: int | None = None) -> tuple[str, int, int]:
    """يكتب tar.gz حتمياً (بلا طابع زمني للضغط) إلى out_path. يرجع (sha256, حجم الأرشيف, عدد الملفات)."""
    limit = MAX_SITE_BYTES if max_bytes is None else max_bytes
    n = 0
    base = str(site_dir)

    def norm(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        ti.mode = 0o755 if (ti.isdir() or ti.mode & 0o111) else 0o644
        return ti

    with open(out_path, "wb") as raw:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=3, mtime=0)
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
            tar.add(base, arcname=f"sites/{name}", recursive=False, filter=norm)
            for dp, dns, fns in os.walk(base):
                dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
                for d in list(dns):
                    full = os.path.join(dp, d)
                    if os.path.islink(full):
                        dns.remove(d)
                        continue
                    tar.add(full, arcname=f"sites/{name}/{os.path.relpath(full, base)}", recursive=False, filter=norm)
                for fn in sorted(fns):
                    full = os.path.join(dp, fn)
                    try:
                        st = os.lstat(full)
                    except OSError:
                        continue
                    if not _is_reg(st.st_mode):
                        continue                            # روابط/أجهزة/مقابس: لا تُحفظ أبداً
                    tar.add(full, arcname=f"sites/{name}/{os.path.relpath(full, base)}", recursive=False, filter=norm)
                    n += 1
                    if raw.tell() > limit:
                        raise SiteTooBig(f"أرشيف الموقع تجاوز {limit // 1048576}MB")
        gz.close()
    size = out_path.stat().st_size
    if size > limit:
        raise SiteTooBig(f"أرشيف الموقع {size // 1048576}MB تجاوز الحدّ {limit // 1048576}MB")
    return sha256_file(out_path), size, n


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def unpack_archive(root: Path, name: str, archive: Path) -> int:
    """فك آمن إلى root/name: ملفات ومجلدات عادية فقط، داخل الجذر حصراً، بحدود لعدد الملفات والحجم.
    يُفكّ في مجلد مؤقت ثم يُنقل (لا يظهر موقع نصف مستعاد أبداً)."""
    root.mkdir(parents=True, exist_ok=True)
    final = root / name
    stage = Path(tempfile.mkdtemp(prefix=f".restore-{name[:8]}-", dir=str(root)))
    prefix = f"sites/{name}"
    base = stage.resolve()
    count = 0
    written = 0
    try:
        with tarfile.open(str(archive), mode="r:gz") as tar:
            for m in tar:
                if m.name != prefix and not m.name.startswith(prefix + "/"):
                    continue
                rel = m.name[len(prefix):].lstrip("/")
                if not rel:
                    continue
                if not (m.isfile() or m.isdir()):
                    continue                                 # روابط صلبة/رمزية/أجهزة: تُتجاهل
                target = (base / rel).resolve()
                if target != base and base not in target.parents:
                    continue                                 # Path Traversal
                count += 1
                if count > MAX_MEMBERS:
                    raise IntegrityError("عدد ملفات الأرشيف يتجاوز الحدّ")
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                written += max(0, m.size)
                if written > MAX_UNPACK_BYTES:
                    raise IntegrityError("حجم الأرشيف بعد الفك يتجاوز الحدّ (قنبلة ضغط؟)")
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(m)
                if src is None:
                    continue
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                try:
                    os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
                    os.utime(target, (m.mtime, m.mtime))
                except OSError:
                    pass
        _install_stage(stage, final)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return count


def _install_stage(stage: Path, final: Path) -> None:
    """ينقل المجلد المستعاد مكان الوجهة. إن وُجد محتوى جديد كتبه المستخدم أثناء الاستعادة لا نلمسه."""
    if final.exists():
        try:
            final.rmdir()                                    # مجلد فارغ (أنشأه site_root) → استبدال ذرّي
        except OSError:
            for p in stage.rglob("*"):                        # فيه ملفات: ندمج المفقود فقط
                rel = p.relative_to(stage)
                dst = final / rel
                if p.is_dir():
                    dst.mkdir(parents=True, exist_ok=True)
                elif not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(dst))
            return
    os.replace(stage, final)


# ══════════════════════════════════════════════════════════════════════════════
#  التعبئة في حزم
# ══════════════════════════════════════════════════════════════════════════════
class Packer:
    """يملأ حزمة بعد أخرى ويرفعها. كل ملف أرشيف يُقسَّم تلقائياً بين حزمتين أو أكثر إن لزم."""

    def __init__(self, io_: PackIO, enc: Enc, gen: int, packs: dict, uploaded: list[int],
                 part_size: int = PART_SIZE, gap: float | None = None):
        self.io, self.enc, self.gen = io_, enc, gen
        self.packs, self.uploaded = packs, uploaded
        self.part = part_size
        self.gap = SEND_GAP if gap is None else gap
        self.buf = bytearray()
        self.k = 0
        self.sent = 0
        self._last = 0.0

    async def add_file(self, path: Path) -> list[list]:
        loc: list[list] = []
        with open(path, "rb") as f:
            while True:
                room = self.part - len(self.buf)
                chunk = f.read(room)
                if not chunk:
                    break
                loc.append([self._next_id(), len(self.buf), len(chunk)])
                self.buf += chunk
                if len(self.buf) >= self.part:
                    await self.flush()
        return loc

    def _next_id(self) -> str:
        return f"{self.gen}-{self.k}"

    async def flush(self) -> None:
        if not self.buf:
            return
        pid = self._next_id()
        raw = bytes(self.buf)
        blob = await asyncio.to_thread(self.enc, raw, pack_aad(pid, len(raw)))
        wait = self.gap - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        rec = await self.io.put(pid, blob)
        self._last = time.monotonic()
        self.uploaded.append(rec["mid"])
        self.packs[pid] = {"mid": rec["mid"], "fid": rec["fid"], "size": len(blob), "raw": len(raw)}
        self.k += 1
        self.sent += 1
        self.buf = bytearray()


# ══════════════════════════════════════════════════════════════════════════════
#  قراءة أرشيف موقع من الحزم (للاستعادة وللتنظيف)
# ══════════════════════════════════════════════════════════════════════════════
class PackReader:
    """يقرأ الحزم عند الحاجة مع كاش صغير (حزمتان) كي لا تُنزَّل الحزمة نفسها لكل موقع فيها."""

    def __init__(self, io_: PackIO, dec: Enc, packs: dict, cache_items: int = 2):
        self.io, self.dec, self.packs = io_, dec, packs
        self._cache: dict[str, bytes] = {}
        self._order: list[str] = []
        self._n = cache_items

    async def raw(self, pack_id: str) -> bytes:
        if pack_id in self._cache:
            return self._cache[pack_id]
        rec = self.packs.get(pack_id)
        if not rec:
            raise IntegrityError(f"الحزمة {pack_id} غير موجودة في الفهرس")
        blob = await self.io.get(rec)
        if rec.get("size") and len(blob) != rec["size"]:
            raise IntegrityError(f"حجم الحزمة {pack_id} لا يطابق الفهرس")
        raw = await asyncio.to_thread(self.dec, blob, pack_aad(pack_id, rec["raw"]))
        if len(raw) != rec["raw"]:
            raise IntegrityError(f"طول محتوى الحزمة {pack_id} لا يطابق الفهرس")
        self._cache[pack_id] = raw
        self._order.append(pack_id)
        while len(self._order) > self._n:
            self._cache.pop(self._order.pop(0), None)
        return raw

    async def site_to_file(self, entry: dict, out: Path) -> None:
        """يجمّع أرشيف موقع من أجزائه إلى ملف ويتحقق من sha256 والحجم."""
        h = hashlib.sha256()
        total = 0
        with open(out, "wb") as f:
            for pid, off, ln in entry["loc"]:
                raw = await self.raw(pid)
                seg = raw[off:off + ln]
                if len(seg) != ln:
                    raise IntegrityError(f"مقطع ناقص من الحزمة {pid}")
                f.write(seg)
                h.update(seg)
                total += ln
        if total != entry["size"] or h.hexdigest() != entry["sha"]:
            raise IntegrityError("بصمة الأرشيف لا تطابق المانيفست (تلف أو تلاعب)")


# ══════════════════════════════════════════════════════════════════════════════
#  التخطيط: ماذا نحفظ، ماذا ندمج، ماذا نحذف
# ══════════════════════════════════════════════════════════════════════════════
def live_bytes(sites: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in sites.values():
        for pid, _off, ln in e["loc"]:
            out[pid] = out.get(pid, 0) + ln
    return out


def plan_gc(sites: dict, packs: dict, protect: set[str]) -> list[str]:
    """أسماء المواقع التي يجب إعادة تعبئتها لأن حزمها فقيرة أو صغيرة متكاثرة. protect = مواقع ستُرفع أصلاً."""
    live = live_bytes(sites)
    cand: list[tuple[float, str]] = []
    small = [pid for pid, r in packs.items() if r["raw"] < SMALL_PACK and live.get(pid, 0) > 0]
    for pid, r in packs.items():
        lv = live.get(pid, 0)
        if lv == 0:
            continue                                         # ميتة تماماً: تُحذف دون إعادة تعبئة
        dead = r["raw"] - lv
        if dead >= 128 * 1024 and lv / r["raw"] < 0.5:
            cand.append((lv / r["raw"], pid))
    if len(small) >= SMALL_PACK_TRIGGER:
        cand += [(0.99, pid) for pid in small]
    if not cand:
        return []
    chosen_packs = {pid for _r, pid in sorted(cand)}
    names: list[str] = []
    budget = GC_MAX_BYTES
    for n, e in sites.items():
        if n in protect:
            continue
        if any(pid in chosen_packs for pid, _o, _l in e["loc"]):
            if e["size"] > budget and names:
                continue
            budget -= e["size"]
            names.append(n)
    return names


def referenced_packs(sites: dict) -> set[str]:
    return {pid for e in sites.values() for pid, _o, _l in e["loc"]}


# ══════════════════════════════════════════════════════════════════════════════
#  الحفظ التزايدي
# ══════════════════════════════════════════════════════════════════════════════
async def save_sites(*, root: Path, old_sites: dict, old_packs: dict, io_: PackIO, enc: Enc, dec: Enc, gen: int,
                     known_ids: set[str] | None, skip_names: set[str], uploaded: list[int],
                     part_size: int = PART_SIZE, gap: float | None = None) -> tuple[dict, dict, list[int], dict]:
    """يرجع (sites, packs, trash_mids, stats). لا يلمس تيليجرام إلا عبر io_. عند أي استثناء يتولى المستدعي حذف uploaded."""
    sites = {k: dict(v) for k, v in old_sites.items()}
    packs = dict(old_packs)
    stats = {"changed": 0, "gc": 0, "removed": 0, "too_big": [], "packs_uploaded": 0, "bytes": 0}

    if known_ids is not None:                                # مواقع حُذفت من القاعدة: تُسقَط من الفهرس
        for n in list(sites):
            if n not in known_ids:
                del sites[n]
                stats["removed"] += 1

    present: list[str] = []
    if root.is_dir():
        present = sorted(p.name for p in root.iterdir()
                         if p.is_dir() and not p.name.startswith(".") and not p.is_symlink())
    changed: list[tuple[str, str, int, int]] = []
    for n in present:
        if n in skip_names:                                  # بارد/تالف: لا نحفظ أبداً فوق نسخته السليمة
            continue
        if known_ids is not None and n not in known_ids:
            continue                                         # مجلد يتيم بلا موقع في القاعدة
        sig, total, cnt = await asyncio.to_thread(site_sig, root / n)
        e = sites.get(n)
        if e is None:
            if cnt == 0:
                continue                                     # موقع جديد فارغ: لا شيء يُحفظ بعد
        elif e.get("sig") == sig:
            continue
        changed.append((n, sig, total, cnt))

    gc_names = plan_gc(sites, packs, {c[0] for c in changed})
    if not changed and not gc_names and not stats["removed"] and set(packs) == referenced_packs(sites):
        return sites, packs, [], stats

    packer = Packer(io_, enc, gen, packs, uploaded, part_size, gap)
    reader = PackReader(io_, dec, old_packs)
    tmpdir = Path(tempfile.mkdtemp(prefix="sawpack-"))
    try:
        for n, sig, _total, _cnt in changed:
            tmp = tmpdir / "a.tgz"
            try:
                sha, size, nfiles = await asyncio.to_thread(build_archive, root / n, n, tmp)
            except SiteTooBig as ex:
                stats["too_big"].append((n, str(ex)))
                continue
            loc = await packer.add_file(tmp)
            sites[n] = {"sha": sha, "size": size, "n": nfiles, "sig": sig, "loc": loc,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            stats["changed"] += 1
            stats["bytes"] += size
            tmp.unlink(missing_ok=True)
        for n in gc_names:
            tmp = tmpdir / "g.tgz"
            e = sites[n]
            await reader.site_to_file(e, tmp)                # يتحقق من sha قبل إعادة التعبئة
            e["loc"] = await packer.add_file(tmp)
            stats["gc"] += 1
            tmp.unlink(missing_ok=True)
        await packer.flush()
        stats["packs_uploaded"] = packer.sent
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    ref = referenced_packs(sites)
    trash = [packs[pid]["mid"] for pid in list(packs) if pid not in ref and pid in old_packs]
    for pid in list(packs):
        if pid not in ref:
            del packs[pid]
    return sites, packs, trash, stats


# ══════════════════════════════════════════════════════════════════════════════
#  الاستعادة (فورية + باردة)
# ══════════════════════════════════════════════════════════════════════════════
async def restore_one(root: Path, name: str, entry: dict, reader: PackReader) -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="sawrest-"))
    try:
        arc = tmpdir / "a.tgz"
        await reader.site_to_file(entry, arc)
        return await asyncio.to_thread(unpack_archive, root, name, arc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def split_eager_cold(sites: dict, *, site_mb: int, total_mb: int, everything: bool = False) -> tuple[list[str], list[str]]:
    """الصغير يُستعاد فوراً حتى ميزانية مجموعة؛ الباقي بارد (يُستعاد عند أول طلب)."""
    if everything:
        order = sorted(sites, key=lambda n: (sites[n]["loc"][0][0] if sites[n]["loc"] else "", sites[n]["loc"][0][1] if sites[n]["loc"] else 0))
        return order, []
    eager, cold = [], []
    budget = total_mb * 1048576
    for n in sorted(sites, key=lambda k: sites[k]["size"]):
        sz = sites[n]["size"]
        if sz <= site_mb * 1048576 and budget - sz >= 0:
            eager.append(n)
            budget -= sz
        else:
            cold.append(n)
    eager.sort(key=lambda n: (sites[n]["loc"][0][0] if sites[n]["loc"] else "", sites[n]["loc"][0][1] if sites[n]["loc"] else 0))
    return eager, cold


async def refresh_sigs(root: Path, sites: dict, names) -> None:
    """بعد الاستعادة تتغيّر أجزاء من الثانية في mtime (tar يخزّن ثواني فقط). نعيد حساب البصمة محلياً كي لا يُعدّ الموقع «متغيّراً»
    فيُعاد رفعه كاملاً بلا سبب."""
    for n in names:
        if n in sites and (root / n).is_dir():
            sites[n]["sig"] = (await asyncio.to_thread(site_sig, root / n))[0]


async def restore_sites(*, root: Path, sites: dict, packs: dict, io_: PackIO, dec: Enc, eager: list[str],
                        time_budget: float = 240.0) -> tuple[list[str], list[str], dict]:
    """يرجع (restored, deferred, bad). deferred = لم تُستعَد لضيق الوقت فتصير باردة."""
    reader = PackReader(io_, dec, packs)
    restored, deferred, bad = [], [], {}
    t0 = time.monotonic()
    for i, n in enumerate(eager):
        if time.monotonic() - t0 > time_budget:
            deferred = eager[i:]
            break
        try:
            await restore_one(root, n, sites[n], reader)
            restored.append(n)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            if isinstance(ex, IntegrityError) or type(ex).__name__ == "InvalidTag":
                bad[n] = f"{type(ex).__name__}: {str(ex)[:160]}"     # تالف فعلاً: لا يُكتب فوقه لاحقاً
            else:
                deferred.append(n)                           # عابر (شبكة/تيليجرام): يبقى بارداً ويُعاد المحاولة عند أول طلب
    return restored, deferred, bad
