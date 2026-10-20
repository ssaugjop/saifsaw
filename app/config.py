"""SAWHost - Config (reads from environment only, zero hardcoded secrets)"""
from __future__ import annotations
import os
from dataclasses import dataclass, field


# ── دخول الأدمن بكلمة مرور (اختياري، ومعطّل افتراضياً) ─────────────────────────
# لا توجد أي بيانات دخول مدمجة في الكود إطلاقاً. الأدمن يدخل بشكل افتراضي عبر
# هويته في تيليجرام فقط (زر اللوحة داخل البوت أو رمز /login)، وهي هوية لا يمكن
# تزويرها لأن تيليجرام يوقّعها. لتفعيل الدخول بكلمة مرور من المتصفح عيّن Secrets:
#   ADMIN_WEB_USER  = اسم المستخدم
#   ADMIN_PASS_HASH = بصمة PBKDF2 (ولّدها بالأمر:  python tools/make_admin_hash.py)
ADMIN_USER = os.getenv("ADMIN_WEB_USER", "").strip()
ADMIN_PASS_HASH = os.getenv("ADMIN_PASS_HASH", "").strip()


def _req(k: str) -> str:
    v = os.getenv(k)
    if not v:
        raise RuntimeError(f"متغير البيئة '{k}' مطلوب ولم يُعيَّن.")
    return v


def _int(k: str, d: int) -> int:
    try: return int(os.getenv(k, str(d)))
    except ValueError: return d


def _host(v: str) -> str:
    """ينظّف اسم مضيف: يزيل البروتوكول والمسار والمنفذ ويحوّله لأحرف صغيرة."""
    v = (v or "").strip().lower()
    for pre in ("https://", "http://"):
        if v.startswith(pre): v = v[len(pre):]
    return v.split("/")[0].split(":")[0].strip(".")


def _bool(k: str, d: bool = False) -> bool:
    v = os.getenv(k, "")
    return v.lower() in {"1","true","yes","on"} if v else d


@dataclass(frozen=True)
class Cfg:
    # Telegram
    bot_token:        str  = field(default_factory=lambda: _req("BOT_TOKEN"))
    bot_username:     str  = field(default_factory=lambda: os.getenv("BOT_USERNAME", "SAWHostBot"))
    admin_id:         int  = field(default_factory=lambda: _int("ADMIN_ID", 7231728679))
    admin_username:   str  = field(default_factory=lambda: os.getenv("ADMIN_USERNAME", "S_SA_IQ"))
    webhook_base_url: str  = field(default_factory=lambda: os.getenv("WEBHOOK_BASE_URL", ""))
    webhook_path:     str  = field(default_factory=lambda: os.getenv("WEBHOOK_PATH", "/webhook"))
    webhook_secret:   str  = field(default_factory=lambda: os.getenv("WEBHOOK_SECRET", ""))
    # DB
    db_url:           str  = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    sqlite_path:      str  = field(default_factory=lambda: os.getenv("SQLITE_PATH", "/data/sawhost.db"))
    # Storage
    sites_root:       str  = field(default_factory=lambda: os.getenv("STORAGE_ROOT", "/data/sites"))
    backups_root:     str  = field(default_factory=lambda: os.getenv("BACKUPS_ROOT", "/data/backups"))
    # Security
    secret_key:       str  = field(default_factory=lambda: _req("SECRET_KEY"))
    rate_limit:       int  = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 30))
    # دخول الأدمن بكلمة مرور (إضافي اختياري عبر المتغيرات؛ الدخول المدمج أدناه يعمل دائماً)
    dash_user:        str  = field(default_factory=lambda: os.getenv("DASHBOARD_USER", ""))
    dash_pass:        str  = field(default_factory=lambda: os.getenv("DASHBOARD_PASS", ""))
    # الدومين العام للمنصة (مثال: sawhost.de5.net) — يُستخدم في كل الروابط التي تُعرض للمستخدمين
    public_domain:    str  = field(default_factory=lambda: _host(os.getenv("PUBLIC_DOMAIN", "")))
    # دومين المواقع المستضافة: كل موقع يُخدَم على <slug>.<SITES_DOMAIN> (أصل مستقل = عزل كامل عن اللوحة).
    # يتطلب سجل DNS wildcard (*.sawhost.de5.net) يمرّ عبر نفس الـ Worker. اتركه فارغاً لاستخدام /s/<slug>.
    sites_domain:     str  = field(default_factory=lambda: _host(os.getenv("SITES_DOMAIN", "")))
    # سرّ مشترك مع الـ Worker/البروكسي الذي أمام المنصة: يسمح بالوثوق بترويسات المضيف وعنوان IP الحقيقي
    edge_secret:      str  = field(default_factory=lambda: os.getenv("EDGE_SECRET", ""))
    # تشغيل تطبيقات (Python/Node/PHP): auto = فقط عند وجود عزل فعلي (تشغيل كـ root لإسقاط الصلاحيات)
    allow_apps:       str  = field(default_factory=lambda: os.getenv("ALLOW_APPS", "auto").strip().lower())
    # App
    port:             int  = field(default_factory=lambda: _int("PORT", 7860))
    log_level:        str  = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    @property
    def async_dsn(self) -> str:
        if self.db_url:
            return self.db_url.replace("postgresql://","postgresql+asyncpg://").replace("postgres://","postgresql+asyncpg://")
        path = self.sqlite_path
        if path.startswith("/data") and not os.path.exists("/data"):
            path = "./sawhost_dev.db"
        return f"sqlite+aiosqlite:///{path}"

    @property
    def is_sqlite(self) -> bool:
        return not bool(self.db_url)

    @property
    def base_url(self) -> str:
        """عنوان الـ Space الفعلي (يستقبل الويب هوك). لا يتغيّر عند ربط دومين خاص."""
        return self.webhook_base_url.rstrip("/")

    @property
    def space_host(self) -> str:
        return _host(self.webhook_base_url)

    @property
    def public_url(self) -> str:
        """العنوان الذي يراه المستخدمون: الدومين الخاص إن عُيّن، وإلا عنوان الـ Space."""
        return f"https://{self.public_domain}" if self.public_domain else self.base_url

    @property
    def platform_hosts(self) -> frozenset[str]:
        """المضيفات التي تُعدّ «المنصة نفسها» (لا مواقع مستضافة)."""
        return frozenset(h for h in (self.public_domain, self.space_host) if h)


_cfg: Cfg | None = None
def get_cfg() -> Cfg:
    global _cfg
    if _cfg is None: _cfg = Cfg()
    return _cfg
