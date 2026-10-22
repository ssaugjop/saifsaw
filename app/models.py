"""SAWHost - All Models (single file)"""
from __future__ import annotations
import enum, uuid
from datetime import datetime, timezone
from typing import Any, ClassVar
from sqlalchemy import (BigInteger, Boolean, DateTime, Enum as SAEnum,
                        ForeignKey, Integer, String, Text)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, TypeDecorator


# ── Type helpers ──────────────────────────────────────────────────────────────
class UUIDType(TypeDecorator):
    impl = CHAR(36); cache_ok = True
    def load_dialect_impl(self, d):
        return d.type_descriptor(PGUUID(as_uuid=True) if d.name=="postgresql" else CHAR(36))
    def process_bind_param(self, v, d): return str(v) if v else None
    def process_result_value(self, v, d): return uuid.UUID(str(v)) if v else None


def _now() -> datetime: return datetime.now(timezone.utc)


def as_aware(dt: datetime | None) -> datetime | None:
    """
    يضمن أن أي datetime قادم من قاعدة البيانات (خاصة SQLite، التي لا تخزّن
    معلومة timezone إطلاقاً بغض النظر عن DateTime(timezone=True) بالتعريف)
    يصبح aware (UTC) قبل أي مقارنة. بدون هذا، أي مقارنة بين datetime من
    القاعدة و datetime.now(timezone.utc) تفشل بـ TypeError.
    """
    if dt is None: return None
    if dt.tzinfo is None: return dt.replace(tzinfo=timezone.utc)
    return dt


class Base(DeclarativeBase): pass


class _ID:
    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)


class _TS:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


# ── Enums ─────────────────────────────────────────────────────────────────────
class UserRole(str, enum.Enum): USER="user"; ADMIN="admin"
class SiteStatus(str, enum.Enum): RUNNING="running"; STOPPED="stopped"; DEPLOYING="deploying"; ERROR="error"; SUSPENDED="suspended"
class DomainType(str, enum.Enum): SUBDOMAIN="subdomain"; CUSTOM="custom"; PLATFORM="platform"
class DomainVerify(str, enum.Enum): PENDING="pending"; VERIFIED="verified"; FAILED="failed"
class BackupTrigger(str, enum.Enum): MANUAL="manual"; AUTO="auto"; SCHEDULED="scheduled"
class BackupStatus(str, enum.Enum): PENDING="pending"; COMPLETED="completed"; FAILED="failed"
class ReqStatus(str, enum.Enum): PENDING="pending"; APPROVED="approved"; REJECTED="rejected"


# ── نظام الخطط (Plans) - ديناميكي بالكامل، يُدار من لوحة الأدمن ───────────────
# لا يوجد أي حدود أو أسعار مكتوبة بالكود (Hardcoded)؛ كل شيء بقاعدة البيانات
# ضمن جدول SubscriptionPlan، ويمكن للأدمن إضافة/حذف/تعديل أي خطة في أي وقت
# وتُطبَّق فوراً على كامل النظام دون الحاجة لإعادة نشر الكود.
FREE_PLAN_CODE = "free"  # الكود المحجوز للخطة المجانية الافتراضية (لا يُحذف)

# ── ثوابت نظام الإحالة والتجربة المجانية ─────────────────────────────────────
# لا توجد أي تجربة تلقائية لمجرد الانضمام - فقط المُحيل يربح وقتاً عند نجاح إحالته
TRIAL_REFERRAL_DAYS = 1    # الوقت الممنوح للمُحيل مقابل كل إحالة ناجحة (أيام)
TRIAL_MAX_DAYS      = 30   # السقف الأقصى لإجمالي أيام التجربة المكتسبة


# ── Models ─────────────────────────────────────────────────────────────────────
class SubscriptionPlan(Base, _ID, _TS):
    """
    خطة اشتراك ديناميكية بالكامل - يديرها الأدمن من لوحة التحكم.
    لا توجد أي قيم مكتوبة بالكود؛ كل خطة (السعر، الحدود، الترتيب) تُخزَّن هنا
    وتُطبَّق فوراً على كل النظام (المستخدمين، الطلبات، لوحة الاشتراك).
    """
    __tablename__ = "subscription_plans"
    code:        Mapped[str]       = mapped_column(String(32), unique=True, index=True)  # معرف ثابت داخلي (free, starter..)
    name:        Mapped[str]       = mapped_column(String(64))                            # الاسم المعروض للمستخدم
    icon:        Mapped[str]       = mapped_column(String(16), default="📦")
    price_monthly:Mapped[int]      = mapped_column(Integer, default=0)                     # السعر الشهري (د.ع)
    max_sites:   Mapped[int]       = mapped_column(Integer, default=1)
    max_mb:      Mapped[int]       = mapped_column(Integer, default=100)
    max_custom_domains: Mapped[int]= mapped_column(Integer, default=0)   # عدد الدومينات المخصصة (ملك المستخدم)
    max_platform_domains: Mapped[int] = mapped_column(Integer, default=0) # عدد دومينات المنصة القابلة للاستخدام
    max_backups: Mapped[int]       = mapped_column(Integer, default=1)
    features:    Mapped[str]       = mapped_column(Text, default="")     # ميزات إضافية نصية (سطر لكل ميزة)
    is_active:   Mapped[bool]      = mapped_column(Boolean, default=True)   # يظهر للمستخدمين أم مخفي
    is_default:  Mapped[bool]      = mapped_column(Boolean, default=False)  # الخطة الافتراضية عند التسجيل (مجانية عادة)
    sort_order:  Mapped[int]       = mapped_column(Integer, default=0)      # ترتيب العرض بقائمة الاشتراكات
    @property
    def feature_list(self) -> list[str]:
        return [f.strip() for f in self.features.splitlines() if f.strip()]


class User(Base, _ID, _TS):
    __tablename__ = "users"
    telegram_id: Mapped[int]       = mapped_column(BigInteger, unique=True, index=True)
    username:    Mapped[str|None]  = mapped_column(String(64),  nullable=True)
    first_name:  Mapped[str|None]  = mapped_column(String(128), nullable=True)
    role:        Mapped[UserRole]  = mapped_column(SAEnum(UserRole,  name="user_role"),  default=UserRole.USER)
    is_banned:   Mapped[bool]      = mapped_column(Boolean, default=False)
    ban_reason:  Mapped[str|None]  = mapped_column(String(512), nullable=True)
    # ── نظام الإحالة (Referral System) ──────────────────────────────────────
    referred_by_id: Mapped[uuid.UUID|None] = mapped_column(UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    referral_count: Mapped[int]     = mapped_column(Integer, default=0)   # عدد الإحالات الناجحة لهذا المستخدم
    # ── طبقة التجربة المجانية (مستقلة تماماً عن الاشتراك المدفوع) ──────────
    # تُضاف كـ "وقت تجربة" منفصل: تمنح صلاحيات أفضل خطة مدفوعة حتى تنتهي،
    # ثم يعود المستخدم تلقائياً لخطته الأصلية (الاشتراك المدفوع لا يُمس إطلاقاً)
    trial_expires_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_days_total: Mapped[int]   = mapped_column(Integer, default=0)   # مجموع أيام التجربة المكتسبة (سقف 30)
    # أي جلسة صدرت قبل هذا الوقت تُعتبر ملغاة (تسجيل الخروج من كل الأجهزة)
    sessions_valid_after: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    sites:       Mapped[list["Site"]]         = relationship(back_populates="owner",        cascade="all,delete-orphan")
    subscription:Mapped["Subscription|None"]  = relationship(back_populates="user", uselist=False, cascade="all,delete-orphan")
    referred_by: Mapped["User|None"]          = relationship("User", remote_side="User.id")
    @property
    def is_admin(self): return self.role == UserRole.ADMIN
    @property
    def is_trial_active(self) -> bool:
        exp = as_aware(self.trial_expires_at)
        return bool(exp and exp > datetime.now(timezone.utc))


class Subscription(Base, _ID, _TS):
    __tablename__ = "subscriptions"
    # يسمح لـ SQLAlchemy بتجاهل أي attribute غير مُعلَن بصيغة Mapped[]/ClassVar[]
    # بأمان تام (طبقة حماية إضافية فوق ClassVar لضمان عدم اعتبار _trial_plan عموداً).
    __allow_unmapped__ = True

    user_id:   Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("users.id",ondelete="CASCADE"), unique=True)
    plan_id:   Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("subscription_plans.id"), index=True)
    expires_at:Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    # تخصيصات فردية اختيارية (تتجاوز حدود الخطة لمستخدم معين دون تغيير الخطة نفسها)
    custom_sites: Mapped[int|None]   = mapped_column(Integer, nullable=True)
    custom_mb:    Mapped[int|None]   = mapped_column(Integer, nullable=True)
    custom_domains:Mapped[int|None]  = mapped_column(Integer, nullable=True)
    custom_platform_domains:Mapped[int|None] = mapped_column(Integer, nullable=True)
    custom_backups:Mapped[int|None]  = mapped_column(Integer, nullable=True)
    user: Mapped["User"] = relationship(back_populates="subscription")
    plan: Mapped["SubscriptionPlan"] = relationship("SubscriptionPlan")

    # حقل غير مخزَّن بقاعدة البيانات على الإطلاق - يُعبَّأ فقط من Subs.get()
    # عندما تكون طبقة التجربة المجانية نشطة، ليمنح مؤقتاً حدود أفضل خطة مدفوعة
    # متاحة دون أي تعديل على plan_id الحقيقي المخزَّن (الاشتراك الأصلي لا يُمس).
    _trial_plan: ClassVar[Any] = None
    # يُعبَّأ من Subs.get() عند انتهاء expires_at: تُنزَّل الحدود للخطة الافتراضية
    # ("بدون اشتراك") دون تعديل السجل المخزَّن، وتُهمَل التخصيصات الفردية.
    _downgrade_plan: ClassVar[Any] = None

    def _effective_plan(self) -> "SubscriptionPlan":
        return self._trial_plan or self._downgrade_plan or self.plan

    @property
    def is_lapsed(self) -> bool:
        return self._downgrade_plan is not None and self._trial_plan is None

    def _c(self, v):
        return None if self.is_lapsed else v

    @property
    def is_trial_boost(self) -> bool:
        return self._trial_plan is not None
    @property
    def max_sites(self):   return self._c(self.custom_sites)   or self._effective_plan().max_sites
    @property
    def max_mb(self):      return self._c(self.custom_mb)      or self._effective_plan().max_mb
    @property
    def max_domains(self): return self._c(self.custom_domains) or self._effective_plan().max_custom_domains
    @property
    def max_platform_domains(self): return self._c(self.custom_platform_domains) or self._effective_plan().max_platform_domains
    @property
    def max_backups(self): return self._c(self.custom_backups) or self._effective_plan().max_backups


class SiteKind(str, enum.Enum):
    STATIC = "static"   # ملفات HTML/CSS/JS ثابتة - تُخدَم مباشرة بلا عملية منفصلة
    PYTHON = "python"   # تطبيق Python (main.py/app.py + requirements.txt) - subprocess معزول
    NODE   = "node"     # تطبيق Node.js (package.json) - subprocess معزول
    PHP    = "php"      # تطبيق PHP (index.php / composer.json) - php -S معزول


class Site(Base, _ID, _TS):
    __tablename__ = "sites"
    owner_id:   Mapped[uuid.UUID]  = mapped_column(UUIDType, ForeignKey("users.id",ondelete="CASCADE"), index=True)
    slug:       Mapped[str]        = mapped_column(String(63), unique=True, index=True)
    name:       Mapped[str]        = mapped_column(String(128))
    status:     Mapped[SiteStatus] = mapped_column(SAEnum(SiteStatus,name="site_status"), default=SiteStatus.STOPPED)
    used_mb:    Mapped[int]        = mapped_column(BigInteger, default=0)
    is_deleted: Mapped[bool]       = mapped_column(Boolean, default=False)
    # ── العزل الحقيقي بـ Subprocess ──────────────────────────────────────────
    kind:        Mapped[SiteKind]  = mapped_column(SAEnum(SiteKind, name="site_kind"), default=SiteKind.STATIC)
    proc_port:   Mapped[int|None]  = mapped_column(Integer, nullable=True)   # منفذ داخلي 127.0.0.1 مخصَّص للعملية
    proc_pid:    Mapped[int|None]  = mapped_column(Integer, nullable=True)   # PID الحالي (لمراقبة/إيقاف العملية)
    last_error:  Mapped[str|None]  = mapped_column(Text, nullable=True)      # آخر خطأ تشغيل (يُعرض للمستخدم)
    owner:   Mapped["User"]        = relationship(back_populates="sites")
    domains: Mapped[list["Domain"]]= relationship(back_populates="site", cascade="all,delete-orphan")
    backups: Mapped[list["Backup"]]= relationship(back_populates="site", cascade="all,delete-orphan")


class GithubLink(Base, _ID, _TS):
    """
    ربط موقع بمستودع GitHub. عند وجود ربط نشط، تُسحَب الملفات من GitHub
    (بدل الرفع اليدوي)، وتتحدث تلقائياً عبر GitHub Webhook عند أي push.
    """
    __tablename__ = "github_links"
    site_id:     Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("sites.id", ondelete="CASCADE"), unique=True, index=True)
    repo_owner:  Mapped[str]       = mapped_column(String(100))
    repo_name:   Mapped[str]       = mapped_column(String(150))
    branch:      Mapped[str]       = mapped_column(String(100), default="main")
    webhook_secret: Mapped[str]    = mapped_column(String(64))   # يُستخدم للتحقق من توقيع GitHub Webhook
    last_sha:    Mapped[str|None]  = mapped_column(String(64), nullable=True)   # آخر commit تمت مزامنته
    last_synced_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str|None] = mapped_column(Text, nullable=True)
    auto_sync:   Mapped[bool]      = mapped_column(Boolean, default=True)   # هل يعمل التحديث التلقائي عبر Webhook
    token_enc:   Mapped[str|None]  = mapped_column(Text, nullable=True)     # توكن GitHub مشفَّر (security.seal) للمستودعات الخاصة
    subdir:      Mapped[str|None]  = mapped_column(String(200), nullable=True)  # مجلد فرعي من المستودع يُنشر كجذر للموقع
    webhook_id:  Mapped[int|None]  = mapped_column(BigInteger, nullable=True)   # معرّف الـ webhook المُنشأ على GitHub (إن أُنشئ تلقائياً)
    manifest:    Mapped[str|None]  = mapped_column(Text, nullable=True)     # JSON: الملفات المنشورة آخر مرة (لحذف المحذوف من المستودع)
    last_commit_msg: Mapped[str|None] = mapped_column(String(300), nullable=True)
    clean:       Mapped[bool]      = mapped_column(Boolean, default=True)    # حذف الملفات المحذوفة من المستودع عند المزامنة
    is_private:  Mapped[bool]      = mapped_column(Boolean, default=False)
    site:        Mapped["Site"]    = relationship("Site")

    @property
    def repo_full(self) -> str:
        return f"{self.repo_owner}/{self.repo_name}"

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo_owner}/{self.repo_name}"


class PlatformDomain(Base, _ID, _TS):
    """
    دومين تملكه المنصة (يضيفه الأدمن فقط)، ويمكن لأي مستخدم عنده رصيد
    كافٍ بخطته اختياره لأحد مواقعه. لا يحتاج تحقق DNS لأنه مُدار مسبقاً
    من الأدمن نفسه على مستوى DNS الرئيسي (Wildcard) للمنصة.
    """
    __tablename__ = "platform_domains"
    hostname: Mapped[str]   = mapped_column(String(255), unique=True, index=True)
    label:    Mapped[str]   = mapped_column(String(128), default="")   # وصف مختصر يظهر للمستخدم
    is_active:Mapped[bool]  = mapped_column(Boolean, default=True)     # متاح للاختيار أم مُعطَّل مؤقتاً
    # site_id الحالي المستخدم لهذا الدومين (None = متاح لأي أحد)، يُدار عبر Domain.platform_domain_id
    used_by_site_id: Mapped[uuid.UUID|None] = mapped_column(UUIDType, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)


class Domain(Base, _ID, _TS):
    __tablename__ = "domains"
    site_id:  Mapped[uuid.UUID]   = mapped_column(UUIDType, ForeignKey("sites.id",ondelete="CASCADE"), index=True)
    hostname: Mapped[str]         = mapped_column(String(255), unique=True, index=True)
    dtype:    Mapped[DomainType]  = mapped_column(SAEnum(DomainType,  name="domain_type"))
    is_primary:Mapped[bool]       = mapped_column(Boolean, default=False)
    verify_status:Mapped[DomainVerify] = mapped_column(SAEnum(DomainVerify,name="domain_verify"), default=DomainVerify.PENDING)
    ssl_active:Mapped[bool]       = mapped_column(Boolean, default=False)
    verify_token:Mapped[str|None] = mapped_column(String(128), nullable=True)
    platform_domain_id: Mapped[uuid.UUID|None] = mapped_column(UUIDType, ForeignKey("platform_domains.id", ondelete="SET NULL"), nullable=True)
    site: Mapped["Site"]          = relationship(back_populates="domains")


class Backup(Base, _ID, _TS):
    __tablename__ = "backups"
    site_id: Mapped[uuid.UUID]      = mapped_column(UUIDType, ForeignKey("sites.id",ondelete="CASCADE"), index=True)
    trigger: Mapped[BackupTrigger]  = mapped_column(SAEnum(BackupTrigger, name="backup_trigger"))
    status:  Mapped[BackupStatus]   = mapped_column(SAEnum(BackupStatus,  name="backup_status"), default=BackupStatus.PENDING)
    path:    Mapped[str|None]       = mapped_column(String(512), nullable=True)
    size_mb: Mapped[int]            = mapped_column(BigInteger, default=0)
    site: Mapped["Site"]            = relationship(back_populates="backups")


class PlatformSetting(Base, _ID, _TS):
    __tablename__ = "platform_settings"
    key:   Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")


class BotText(Base, _ID, _TS):
    __tablename__ = "bot_texts"
    key:  Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value:Mapped[str] = mapped_column(Text)
    desc: Mapped[str] = mapped_column(String(256), default="")


class SubRequest(Base, _ID, _TS):
    __tablename__ = "sub_requests"
    user_id: Mapped[uuid.UUID]  = mapped_column(UUIDType, ForeignKey("users.id",ondelete="CASCADE"), index=True)
    plan_id: Mapped[uuid.UUID]  = mapped_column(UUIDType, ForeignKey("subscription_plans.id"), index=True)
    months:  Mapped[int]        = mapped_column(Integer, default=1)   # عدد الأشهر المطلوب اشتراكها
    status:  Mapped[ReqStatus]  = mapped_column(SAEnum(ReqStatus,  name="req_status"), default=ReqStatus.PENDING, index=True)
    note:    Mapped[str|None]   = mapped_column(Text, nullable=True)
    admin_note:Mapped[str|None] = mapped_column(Text, nullable=True)
    user: Mapped["User"]        = relationship("User")
    plan: Mapped["SubscriptionPlan"] = relationship("SubscriptionPlan")
    @property
    def plan_label(self): return self.plan.name
    @property
    def total_price(self): return self.plan.price_monthly * self.months


class AdminNotif(Base, _ID, _TS):
    """إشعارات مُخزَّنة للأدمن — تعمل بدون اتصال صادر (Piggybacking)"""
    __tablename__ = "admin_notifs"
    text:    Mapped[str]  = mapped_column(Text)
    kind:    Mapped[str]  = mapped_column(String(32), default="info")  # info, user_join, sub_request, warning
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)


class GithubSync(Base, _ID, _TS):
    """سجل مزامنات GitHub (آخر العمليات لكل موقع)."""
    __tablename__ = "github_syncs"
    site_id: Mapped[uuid.UUID]  = mapped_column(UUIDType, ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    sha:     Mapped[str|None]   = mapped_column(String(64), nullable=True)
    message: Mapped[str|None]   = mapped_column(String(300), nullable=True)
    trigger: Mapped[str]        = mapped_column(String(16), default="manual")   # manual | webhook | import
    ok:      Mapped[bool]       = mapped_column(Boolean, default=True)
    files:   Mapped[int]        = mapped_column(Integer, default=0)
    detail:  Mapped[str|None]   = mapped_column(Text, nullable=True)


class AuditLog(Base, _ID, _TS):
    """سجل تدقيق لإجراءات الأدمن الحساسة."""
    __tablename__ = "audit_logs"
    actor:   Mapped[str]        = mapped_column(String(64), default="")
    action:  Mapped[str]        = mapped_column(String(64), index=True)
    target:  Mapped[str|None]   = mapped_column(String(128), nullable=True)
    detail:  Mapped[str|None]   = mapped_column(Text, nullable=True)
    ip:      Mapped[str|None]   = mapped_column(String(64), nullable=True)
