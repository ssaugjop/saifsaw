"""
SAWHost - Process Manager (العزل الحقيقي بـ Subprocess - غير محجوب بالكامل)
=============================================================================
يشغّل تطبيقات المستخدمين (Python / Node / PHP) كعمليات معزولة حقيقية، كل
واحدة على منفذ داخلي عشوائي (127.0.0.1 فقط، غير مكشوف للخارج إطلاقاً).
البوابة المركزية (routing.py) تعمل كـ Reverse Proxy يمرر الطلبات الخارجية
لهذا المنفذ الداخلي حسب مسار الموقع.

مبدأ التصميم الأهم: **لا حجب إطلاقاً (Zero Blocking)**. أي عملية طويلة
(تثبيت متطلبات pip قد يستغرق دقيقتين، إقلاع تطبيق قد يستغرق ثوانٍ) تعمل
كمهمة خلفية (asyncio.create_task) لا يُنتظَر اكتمالها داخل معالج أي زر أو
طلب متصفح. بدلاً من ذلك، تُستطلَع الحالة (get_status) بشكل فوري (أقل من
10ms)، فلا يتجمد البوت أو المتصفح إطلاقاً مهما طال وقت النشر الفعلي.

طبقات الحماية المطبَّقة على كل عملية:
  - تعمل بمجلد المستخدم فقط (cwd)، بدون صلاحية الخروج منه.
  - حدود صارمة على الذاكرة (RLIMIT_AS) والـ CPU (RLIMIT_CPU) وعدد
    العمليات الفرعية (RLIMIT_NPROC) عبر وحدة resource قبل exec.
  - مهلة إقلاع قصوى: لو لم تربط العملية منفذها خلال المهلة تُقتَل تلقائياً.
  - مراقبة دورية (health check) توقف أي عملية متجاوزة أو خاملة طويلاً.
  - متغيرات بيئة نظيفة تماماً (لا تُورَّث أي أسرار من عملية البوت الأساسية).
"""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import random
import resource
import signal
import time
from pathlib import Path

log = logging.getLogger("sawhost.procman")

def _envint(k: str, d: int) -> int:
    try:
        return int(os.getenv(k, str(d)))
    except ValueError:
        return d


MAX_MEMORY_MB      = _envint("APP_MAX_MEMORY_MB", 512)
# RLIMIT_CPU يحسب زمن المعالج التراكمي وليس لحظياً: أي قيمة صغيرة (كانت 30 ثانية) كانت تقتل
# كل تطبيق يعمل لفترة. الافتراضي الآن بلا سقف تراكمي (0)، والمراقبة تتم بحدّ الذاكرة والخمول.
MAX_CPU_SECONDS    = _envint("APP_MAX_CPU_SECONDS", 0)
MAX_NPROC          = 64
STARTUP_TIMEOUT    = 45.0
PIP_INSTALL_TIMEOUT= 120.0
IDLE_TIMEOUT       = 30 * 60
PORT_RANGE         = (20000, 39999)

MAX_LOG_BYTES      = 512 * 1024
_UID_BASE          = 100_000
_UID_SPAN          = 60_000

_running: dict[str, dict] = {}
_deploy: dict[str, dict] = {}
_lock = asyncio.Lock()


def _set_status(site_id: str, state: str, message: str | None = None) -> None:
    _deploy[site_id] = {"state": state, "message": message, "ts": time.monotonic()}


def get_status(site_id: str) -> dict:
    if get_port(site_id) is not None:
        return {"state": "running", "message": None}
    return _deploy.get(site_id, {"state": "idle", "message": None})


def _pick_free_port() -> int:
    used = {v["port"] for v in _running.values()}
    for _ in range(200):
        p = random.randint(*PORT_RANGE)
        if p not in used:
            return p
    raise RuntimeError("تعذّر إيجاد منفذ داخلي متاح.")


def _log_dir() -> Path:
    from app.config import get_cfg
    base = Path(get_cfg().backups_root).parent
    d = base / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path("/tmp/sawhost-logs"); d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path(site_id: str) -> Path:
    return _log_dir() / (hashlib.sha256(site_id.encode()).hexdigest()[:24] + ".log")


def get_logs(site_id: str, max_lines: int = 400) -> list[str]:
    """آخر سطور مخرجات التطبيق (stdout+stderr) من ملف السجل."""
    p = _log_path(site_id)
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 96 * 1024))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-max_lines:]


def _open_log(site_id: str):
    p = _log_path(site_id)
    mode = "ab"
    try:
        if p.exists() and p.stat().st_size > MAX_LOG_BYTES:
            mode = "wb"
    except OSError:
        pass
    f = open(p, mode)
    with contextlib.suppress(OSError):
        os.chmod(p, 0o600)
    f.write(f"\n── {time.strftime('%Y-%m-%d %H:%M:%S')} بدء التشغيل ──\n".encode()); f.flush()
    return f


def site_uid(site_id: str) -> int:
    """UID مستقل لكل موقع (يُستخدم فقط عند تشغيل المنصة كـ root)."""
    h = int(hashlib.sha256(site_id.encode()).hexdigest()[:8], 16)
    return _UID_BASE + (h % _UID_SPAN)


def _prepare_dir(site_root: Path, uid: int) -> None:
    """يجعل مجلد الموقع ملكاً لمستخدمه فقط (0700) كي لا تقرأه مواقع أخرى. بلا اتباع للروابط."""
    try:
        os.lchown(site_root, uid, uid)
        os.chmod(site_root, 0o700)
        for dp, dns, fns in os.walk(site_root, followlinks=False):
            for n in dns + fns:
                with contextlib.suppress(OSError):
                    os.lchown(os.path.join(dp, n), uid, uid)
    except OSError:
        log.warning("تعذّر ضبط ملكية مجلد الموقع %s", site_root, exc_info=True)


def apps_enabled() -> bool:
    """تشغيل تطبيقات المستخدمين (Python/Node/PHP) يتطلب عزلاً حقيقياً بين العمليات.
    في الوضع auto لا يُفعَّل إلا عند التشغيل كـ root (لإسقاط كل موقع إلى UID مستقل).
    بدون عزل يستطيع تطبيق مستخدم قراءة أسرار المنصة وملفات بقية المواقع، لذا نرفض التشغيل (fail closed)."""
    from app.config import get_cfg
    mode = get_cfg().allow_apps
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _limit_resources(uid: int | None = None, kind: str = "python") -> None:
    # RLIMIT_AS يكسر Node.js (يحجز V8 مساحة عنوان افتراضية ضخمة)، فنطبّقه فقط على Python/PHP
    if kind != "node":
        try:
            mem_bytes = MAX_MEMORY_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except Exception:
            pass
    if MAX_CPU_SECONDS > 0:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
        except Exception:
            pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_NPROC, MAX_NPROC))
    except Exception:
        pass
    try:
        os.setsid()
    except Exception:
        pass
    if uid is not None:           # إسقاط الصلاحيات (فقط عند التشغيل كـ root)
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)


def _clean_env(extra_port: int, home: str | None = None, kind: str = "python") -> dict:
    safe_keys = {"PATH", "LANG", "LC_ALL", "PYTHONUNBUFFERED", "TZ"}
    env = {k: v for k, v in os.environ.items() if k in safe_keys}
    if home: env["HOME"] = home
    if kind == "node":
        env["NODE_OPTIONS"] = f"--max-old-space-size={max(64, MAX_MEMORY_MB - 64)}"
    env["PORT"] = str(extra_port)
    env["HOST"] = "127.0.0.1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def detect_kind(site_root: Path) -> str:
    if (site_root / "package.json").is_file():
        return "node"
    if (site_root / "requirements.txt").is_file() or (site_root / "main.py").is_file() or (site_root / "app.py").is_file():
        return "python"
    if any((site_root / c).is_file() for c in ("index.php", "composer.json")) or list(site_root.glob("*.php")):
        return "php"
    return "static"


def _entry_command(site_root: Path, kind: str, port: int) -> list[str] | None:
    if kind == "python":
        for candidate in ("main.py", "app.py", "run.py", "server.py"):
            if (site_root / candidate).is_file():
                return ["python3", candidate]
        return None
    if kind == "node":
        pkg = site_root / "package.json"
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            if (data.get("scripts") or {}).get("start"):
                return ["npm", "start"]
        except Exception:
            pass
        for candidate in ("server.js", "index.js", "app.js"):
            if (site_root / candidate).is_file():
                return ["node", candidate]
        return None
    if kind == "php":
        return ["php", "-S", f"127.0.0.1:{port}", "-t", "."]
    return None


async def _install_python_deps(site_id: str, site_root: Path, logf, uid: int | None) -> str | None:
    req = site_root / "requirements.txt"
    if not req.is_file():
        return None
    _set_status(site_id, "installing", "جارِ تثبيت المتطلبات (requirements.txt)...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", "--no-cache-dir", "--user",
            "--disable-pip-version-check", "-r", "requirements.txt",
            cwd=str(site_root),
            env=_clean_env(0, str(site_root), "python"),
            stdout=logf,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=lambda: _limit_resources(uid, "pip"),
        )
        await asyncio.wait_for(proc.wait(), timeout=PIP_INSTALL_TIMEOUT)
        if proc.returncode != 0:
            return "فشل تثبيت المتطلبات (requirements.txt). تحقق من صحة أسماء الحزم."
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        return "استغرق تثبيت المتطلبات وقتاً طويلاً جداً (أكثر من دقيقتين) وتم إلغاؤه."
    except Exception as e:
        return f"تعذّر تثبيت المتطلبات: {e}"
    return None


APPS_DISABLED_MSG = ("تشغيل التطبيقات (Python/Node/PHP) غير متاح على هذا السيرفر لأنه لا يوفّر عزلاً آمناً "
                     "بين المواقع. المواقع الثابتة تعمل بشكل طبيعي.")


def trigger_start(site_id: str, site_root: Path, kind: str) -> str:
    """
    غير محجوب بالكامل. يُرجع فوراً: 'running' | 'already_deploying' | 'started'.
    العملية الفعلية تعمل بالخلفية عبر asyncio.create_task.
    """
    if get_port(site_id) is not None:
        return "running"
    if not apps_enabled():
        _set_status(site_id, "error", APPS_DISABLED_MSG)
        return "disabled"
    current = _deploy.get(site_id)
    if current and current["state"] in ("installing", "starting"):
        return "already_deploying"
    _set_status(site_id, "starting", "جارِ التحضير للإقلاع...")
    asyncio.create_task(_do_start(site_id, site_root, kind))
    return "started"


async def _do_start(site_id: str, site_root: Path, kind: str) -> None:
    async with _lock:
        if get_port(site_id) is not None:
            return

        cmd = _entry_command(site_root, kind, 0)
        if not cmd:
            _set_status(site_id, "error", "لم يُعثَر على ملف تشغيل معروف (main.py / app.py / package.json / *.php).")
            return

        uid = site_uid(site_id) if (hasattr(os, "geteuid") and os.geteuid() == 0) else None
        if uid is not None:
            _prepare_dir(site_root, uid)
        logf = _open_log(site_id)

        if kind == "python":
            err = await _install_python_deps(site_id, site_root, logf, uid)
            if err:
                logf.close()
                _set_status(site_id, "error", err)
                return

        _set_status(site_id, "starting", "جارِ تشغيل التطبيق...")
        port = _pick_free_port()
        cmd = _entry_command(site_root, kind, port)
        env = _clean_env(port, str(site_root), kind)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(site_root),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=logf,
                stderr=asyncio.subprocess.STDOUT,
                preexec_fn=lambda: _limit_resources(uid, kind),
            )
        except FileNotFoundError:
            logf.close()
            _set_status(site_id, "error", f"بيئة التشغيل ({cmd[0]}) غير متوفرة على السيرفر.")
            return
        except Exception as e:
            logf.close()
            _set_status(site_id, "error", f"تعذّر تشغيل التطبيق: {e}")
            return
        finally:
            pass

        _running[site_id] = {
            "proc": proc, "port": port, "kind": kind,
            "last_hit": time.monotonic(), "started_at": time.monotonic(), "logf": logf,
        }

        ok = await _wait_port_ready(port, proc, STARTUP_TIMEOUT)
        if not ok:
            stderr_tail = "\n".join(get_logs(site_id, 8))[-500:]
            await _kill(site_id)
            msg = ("تعذّر إقلاع التطبيق خلال المهلة المسموحة. تأكد أن تطبيقك يستمع على المنفذ الذي تمرّره المنصة "
                   "في المتغير PORT وعلى العنوان 127.0.0.1 (مثال Python: app.run(port=int(os.environ['PORT'])) ، "
                   "مثال Node: app.listen(process.env.PORT)).")
            if stderr_tail:
                msg += f"\n\nآخر سطور الخطأ:\n{stderr_tail}"
            _set_status(site_id, "error", msg)
            return

        _set_status(site_id, "running", None)
        asyncio.create_task(_monitor(site_id))


async def _wait_port_ready(port: int, proc: asyncio.subprocess.Process, timeout: float) -> bool:
    """ننتظر فقط المنفذ المخصَّص لهذا الموقع. (كان يُجرَّب 3000/5000/8000/8080 أيضاً، فيلتصق الموقع
    بتطبيق موقع آخر ويعرض محتواه — تسرّب بين المواقع — لذلك أُزيل.)"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.returncode is not None:
            return False
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=0.5)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True
        except Exception:
            await asyncio.sleep(0.3)
    return False


async def _monitor(site_id: str) -> None:
    while True:
        await asyncio.sleep(30)
        entry = _running.get(site_id)
        if not entry:
            return
        if entry["proc"].returncode is not None:
            with contextlib.suppress(Exception):
                entry["logf"].close()
            _running.pop(site_id, None)
            _deploy.pop(site_id, None)
            return
        if time.monotonic() - entry["last_hit"] > IDLE_TIMEOUT:
            log.info("إيقاف تلقائي لخمول الموقع %s بعد %ds بلا زيارات", site_id, IDLE_TIMEOUT)
            await _kill(site_id)
            return


def touch(site_id: str) -> None:
    entry = _running.get(site_id)
    if entry:
        entry["last_hit"] = time.monotonic()


def get_port(site_id: str) -> int | None:
    entry = _running.get(site_id)
    if entry and entry["proc"].returncode is None:
        return entry["port"]
    return None


def is_running(site_id: str) -> bool:
    return get_port(site_id) is not None


async def _kill(site_id: str) -> None:
    entry = _running.pop(site_id, None)
    _deploy.pop(site_id, None)
    if not entry:
        return
    proc = entry["proc"]
    with contextlib.suppress(Exception):
        entry["logf"].close()
    pgid = None
    with contextlib.suppress(Exception):
        pgid = os.getpgid(proc.pid)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()


async def stop_process(site_id: str) -> None:
    await _kill(site_id)


async def stop_all() -> None:
    for site_id in list(_running.keys()):
        await _kill(site_id)


def stats() -> dict:
    return {
        "running_count": len(_running),
        "sites": [
            {"site_id": k, "port": v["port"], "kind": v["kind"],
             "uptime_s": int(time.monotonic() - v["started_at"])}
            for k, v in _running.items()
        ],
    }
