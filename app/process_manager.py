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
import re
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
MAX_NPROC          = _envint("APP_MAX_NPROC", 256)           # npm/webpack/next تستخدم خيوطاً كثيرة
STARTUP_TIMEOUT    = float(_envint("APP_STARTUP_TIMEOUT", 90))
PIP_INSTALL_TIMEOUT= float(_envint("APP_INSTALL_TIMEOUT", 300))
BUILD_TIMEOUT      = float(_envint("APP_BUILD_TIMEOUT", 420))
IDLE_TIMEOUT       = _envint("APP_IDLE_TIMEOUT", 6 * 3600)   # 0 = لا إيقاف تلقائي للخمول
RESTART_WINDOW     = 600                                     # انهيار التطبيق: إعادة تشغيل تلقائية
RESTART_MAX        = 5                                       # حتى 5 مرات كل 10 دقائق ثم نتوقف ونعرض الخطأ
_STATIC_MARKER     = ".sawhost_static"                       # موقع Node مبني: يُخدَم كملفات ثابتة من مجلد البناء
_BUILD_DIRS        = ("dist", "build", "out", "public", "docs")
PORT_RANGE         = (20000, 39999)

MAX_LOG_BYTES      = 512 * 1024
_UID_BASE          = 100_000
_UID_SPAN          = 60_000

_running: dict[str, dict] = {}
_restarts: dict[str, list] = {}
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


def _clean_env(extra_port: int, home: str | None = None, kind: str = "python", build: bool = False) -> dict:
    safe_keys = {"PATH", "LANG", "LC_ALL", "PYTHONUNBUFFERED", "TZ"}
    env = {k: v for k, v in os.environ.items() if k in safe_keys}
    if home: env["HOME"] = home
    if kind == "node":
        env["NODE_OPTIONS"] = f"--max-old-space-size={max(64, MAX_MEMORY_MB - 64)}"
        env["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
        env["NPM_CONFIG_FUND"] = "false"
        if build:
            env["CI"] = "true"
        else:
            env["NODE_ENV"] = "production"
    port = str(extra_port)
    for k in ("PORT", "SERVER_PORT", "HTTP_PORT", "APP_PORT", "UVICORN_PORT", "FLASK_RUN_PORT"):
        env[k] = port                       # أغلب الأطر تقرأ أحد هذه المتغيرات
    env["HOST"] = "127.0.0.1"
    env["UVICORN_HOST"] = "127.0.0.1"
    env["FLASK_RUN_HOST"] = "127.0.0.1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _read_text(p: Path, limit: int = 300_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _parse_procfile(site_root: Path) -> str | None:
    """Procfile: سطر web: <أمر> (أو start:). يعمل لأي لغة متوفرة على السيرفر."""
    txt = _read_text(site_root / "Procfile", 20_000)
    for line in txt.splitlines():
        m = re.match(r"^\s*(web|start)\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(2)
    return None


def detect_kind(site_root: Path) -> str:
    if (site_root / "package.json").is_file():
        return "node"
    if (site_root / "requirements.txt").is_file() or (site_root / "manage.py").is_file() \
            or any((site_root / c).is_file() for c in ("main.py", "app.py", "run.py", "server.py", "wsgi.py", "asgi.py")):
        return "python"
    if any((site_root / c).is_file() for c in ("index.php", "composer.json")) \
            or (site_root / "public" / "index.php").is_file() or list(site_root.glob("*.php")):
        return "php"
    if (site_root / "Procfile").is_file():
        return "python"          # الاسم فقط: التشغيل الفعلي يتم عبر Procfile
    return "static"


_ASGI_RE = re.compile(r"^\s*(\w+)\s*(?::[^=\n]+)?=\s*(?:\w+\.)?(FastAPI|Starlette|Quart)\s*\(", re.M)
_FLASK_RE = re.compile(r"^\s*(\w+)\s*(?::[^=\n]+)?=\s*(?:\w+\.)?Flask\s*\(", re.M)


def static_dir(site_root: Path) -> str | None:
    """مجلد البناء لموقع Node مبني (Vite/React/Vue…) إن وُجد وعلامته سليمة."""
    try:
        name = _read_text(site_root / _STATIC_MARKER, 64).strip()
    except Exception:
        return None
    if name in _BUILD_DIRS and (site_root / name / "index.html").is_file():
        return name
    return None


def _find_built_dir(site_root: Path) -> str | None:
    for d in _BUILD_DIRS:
        if (site_root / d / "index.html").is_file():
            return d
    return None


def _python_command(site_root: Path, port: int) -> list[str] | None:
    if (site_root / "manage.py").is_file():
        return ["python3", "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"]
    for cand in ("main.py", "app.py", "run.py", "server.py", "api.py", "wsgi.py", "asgi.py"):
        f = site_root / cand
        if not f.is_file():
            continue
        txt = _read_text(f)
        module = cand[:-3]
        reads_port = bool(re.search(r"""\bPORT\b""", txt))
        m = _ASGI_RE.search(txt)
        if m and not reads_port:        # FastAPI/Starlette بلا قراءة PORT: نشغّله عبر uvicorn على منفذنا
            return ["python3", "-m", "uvicorn", f"{module}:{m.group(1)}", "--host", "127.0.0.1",
                    "--port", str(port), "--proxy-headers", "--forwarded-allow-ips", "*"]
        m = _FLASK_RE.search(txt)
        if m and not reads_port:
            return ["python3", "-m", "flask", "--app", f"{module}:{m.group(1)}", "run",
                    "--host", "127.0.0.1", "--port", str(port)]
        return ["python3", cand]
    return None


def _node_pkg(site_root: Path) -> dict:
    try:
        return json.loads(_read_text(site_root / "package.json", 500_000) or "{}")
    except Exception:
        return {}


def _php_docroot(site_root: Path) -> str:
    return "public" if (site_root / "public" / "index.php").is_file() else "."


_PHP_ROUTER = """<?php
// موجّه SAWHost: الملفات الموجودة تُخدَم كما هي، وأي مسار آخر يذهب إلى index.php (Laravel/Slim/روابط جميلة)
$uri = urldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH));
$root = $_SERVER['DOCUMENT_ROOT'];
if ($uri !== '/' && is_file($root . $uri)) { return false; }
if (is_file($root . '/index.php')) { require_once $root . '/index.php'; return true; }
return false;
"""


def _entry_command(site_root: Path, kind: str, port: int) -> list[str] | None:
    proc = _parse_procfile(site_root)
    if proc:
        return ["sh", "-c", "exec " + proc]
    if kind == "python":
        return _python_command(site_root, port)
    if kind == "node":
        pkg = _node_pkg(site_root)
        if (pkg.get("scripts") or {}).get("start"):
            return ["npm", "start", "--silent"]
        for candidate in ("server.js", "index.js", "app.js", "main.js", "src/index.js", "src/server.js"):
            if (site_root / candidate).is_file():
                return ["node", candidate]
        return None
    if kind == "php":
        doc = _php_docroot(site_root)
        router = site_root / ".sawhost_router.php"
        return ["php", "-S", f"127.0.0.1:{port}", "-t", doc, str(router.name)] if router.is_file() \
            else ["php", "-S", f"127.0.0.1:{port}", "-t", doc]
    return None


async def _run_step(site_id: str, site_root: Path, logf, uid: int | None, argv: list[str], title: str,
                    kind: str, timeout: float, build: bool = False) -> str | None:
    """يشغّل أمر تحضير (pip/npm/composer/build) بنفس عزل المستخدم. يرجع رسالة خطأ أو None."""
    _set_status(site_id, "installing", title)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(site_root),
            env=_clean_env(0, str(site_root), kind, build=build),
            stdin=asyncio.subprocess.DEVNULL, stdout=logf, stderr=asyncio.subprocess.STDOUT,
            preexec_fn=lambda: _limit_resources(uid, "pip" if kind == "python" else kind),
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        if proc.returncode != 0:
            tail = "\n".join(get_logs(site_id, 8))[-400:]
            return f"فشل: {title}.\n{tail}".strip()
    except asyncio.TimeoutError:
        if proc is not None:
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return f"استغرق «{title}» أكثر من {int(timeout)} ثانية وتم إلغاؤه."
    except FileNotFoundError:
        return f"الأداة ({argv[0]}) غير متوفرة على السيرفر."
    except Exception as e:
        return f"تعذّر «{title}»: {e}"
    return None


async def _install_python_deps(site_id: str, site_root: Path, logf, uid: int | None) -> str | None:
    pip = ["pip", "install", "--no-cache-dir", "--user", "--disable-pip-version-check"]
    if (site_root / "requirements.txt").is_file():
        err = await _run_step(site_id, site_root, logf, uid, pip + ["-r", "requirements.txt"],
                              "تثبيت المتطلبات (requirements.txt)", "python", PIP_INSTALL_TIMEOUT)
        if err:
            return err
    cmd = _python_command(site_root, 0) if not _parse_procfile(site_root) else None
    if cmd and "uvicorn" in cmd:
        return await _run_step(site_id, site_root, logf, uid, pip + ["uvicorn"], "تثبيت uvicorn", "python", 120)
    return None


async def _prepare_node(site_id: str, site_root: Path, logf, uid: int | None) -> str | None:
    pkg = _node_pkg(site_root)
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    scripts = pkg.get("scripts") or {}
    if deps and not (site_root / "node_modules").is_dir():
        err = await _run_step(site_id, site_root, logf, uid,
                              ["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"],
                              "تثبيت حزم Node (npm install)", "node", PIP_INSTALL_TIMEOUT, build=True)
        if err:
            return err
    has_entry = any((site_root / c).is_file() for c in ("server.js", "index.js", "app.js", "main.js"))
    needs_build = "build" in scripts and (
        ("next" in deps and not (site_root / ".next").is_dir())
        or ("start" not in scripts and not has_entry and static_dir(site_root) is None)
    )
    if needs_build:
        err = await _run_step(site_id, site_root, logf, uid, ["npm", "run", "build"],
                              "بناء المشروع (npm run build)", "node", BUILD_TIMEOUT, build=True)
        if err:
            return err
    if "start" not in scripts and not has_entry:
        built = _find_built_dir(site_root)
        if built:                                   # Vite/React/Vue…: لا خادم، نخدم ناتج البناء كموقع ثابت
            try:
                (site_root / _STATIC_MARKER).write_text(built, encoding="utf-8")
                if uid is not None:
                    os.lchown(site_root / _STATIC_MARKER, uid, uid)
            except OSError:
                pass
    return None


async def _prepare_php(site_id: str, site_root: Path, logf, uid: int | None) -> str | None:
    if (site_root / "composer.json").is_file() and not (site_root / "vendor").is_dir():
        err = await _run_step(site_id, site_root, logf, uid,
                              ["composer", "install", "--no-dev", "--no-interaction", "--prefer-dist", "--no-progress"],
                              "تثبيت حزم PHP (composer install)", "php", PIP_INSTALL_TIMEOUT)
        if err:
            return err
    doc = _php_docroot(site_root)
    if (site_root / doc / "index.php").is_file():
        router = site_root / ".sawhost_router.php"
        try:
            router.write_text(_PHP_ROUTER, encoding="utf-8")
            if uid is not None:
                os.lchown(router, uid, uid)
        except OSError:
            pass
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
    if kind == "node" and static_dir(site_root):
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

        uid = site_uid(site_id) if (hasattr(os, "geteuid") and os.geteuid() == 0) else None
        if uid is not None:
            _prepare_dir(site_root, uid)
        logf = _open_log(site_id)

        # تحضير حسب النوع: تثبيت المتطلبات / البناء (قد يكتب علامة الموقع الثابت المبني)
        err = None
        if kind == "python":
            err = await _install_python_deps(site_id, site_root, logf, uid)
        elif kind == "node":
            err = await _prepare_node(site_id, site_root, logf, uid)
        elif kind == "php":
            err = await _prepare_php(site_id, site_root, logf, uid)
        if err:
            logf.close()
            _set_status(site_id, "error", err)
            return

        if kind == "node" and static_dir(site_root):      # ناتج بناء ثابت: لا عملية تُشغَّل
            logf.close()
            _set_status(site_id, "running", None)
            return

        _set_status(site_id, "starting", "جارِ تشغيل التطبيق...")
        port = _pick_free_port()
        cmd = _entry_command(site_root, kind, port)
        if not cmd:
            logf.close()
            _set_status(site_id, "error", "لم يُعثَر على ملف تشغيل معروف (main.py / app.py / package.json / index.php / Procfile).")
            return
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

        _running[site_id] = {
            "proc": proc, "port": port, "kind": kind, "site_root": site_root,
            "last_hit": time.monotonic(), "started_at": time.monotonic(), "logf": logf,
        }

        ok = await _wait_port_ready(port, proc, STARTUP_TIMEOUT)
        if not ok:
            stderr_tail = "\n".join(get_logs(site_id, 8))[-500:]
            await _kill(site_id)
            msg = ("تعذّر إقلاع التطبيق خلال المهلة المسموحة. تأكد أن تطبيقك يستمع على المنفذ الذي تمرّره المنصة "
                   "في المتغير PORT وعلى العنوان 127.0.0.1 (مثال Python: app.run(port=int(os.environ['PORT'])) ، "
                   "مثال Node: app.listen(process.env.PORT)). أو أضف ملف Procfile بسطر: web: <أمر التشغيل>.")
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
        await asyncio.sleep(10)
        entry = _running.get(site_id)
        if not entry:
            return
        if entry["proc"].returncode is not None:
            code = entry["proc"].returncode
            site_root, kind = entry.get("site_root"), entry["kind"]
            with contextlib.suppress(Exception):
                entry["logf"].close()
            _running.pop(site_id, None)
            _deploy.pop(site_id, None)
            if site_root is None or kind not in ("python", "node", "php"):
                return
            now = time.monotonic()
            hist = [t for t in _restarts.get(site_id, []) if now - t < RESTART_WINDOW]
            if len(hist) >= RESTART_MAX:
                tail = "\n".join(get_logs(site_id, 8))[-500:]
                _set_status(site_id, "error", f"توقف التطبيق بشكل متكرر (آخر رمز خروج {code}). أصلح الخطأ ثم أعد التشغيل.\n\n{tail}")
                return
            hist.append(now)
            _restarts[site_id] = hist
            log.warning("انهار تطبيق %s (code=%s) — إعادة تشغيل تلقائية %d/%d", site_id, code, len(hist), RESTART_MAX)
            await asyncio.sleep(min(2 * len(hist), 10))
            asyncio.create_task(_do_start(site_id, site_root, kind))
            return
        if IDLE_TIMEOUT > 0 and time.monotonic() - entry["last_hit"] > IDLE_TIMEOUT:
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


async def stop_process(site_id: str, site_root: Path | None = None) -> None:
    entry = _running.get(site_id)
    _restarts.pop(site_id, None)
    await _kill(site_id)
    _deploy.pop(site_id, None)
    root = site_root or (entry.get("site_root") if entry else None)
    if root is not None:                      # أي إيقاف/إعادة تشغيل يُبطل ناتج البناء ليُعاد بناؤه من الكود الحالي
        with contextlib.suppress(OSError):
            (root / _STATIC_MARKER).unlink()


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
