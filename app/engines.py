"""SAWHost - All Engines (auth, hosting, storage, domain, backup, subscription, admin, text, notifications)"""
from __future__ import annotations

import asyncio, hashlib, html as _h, io, re, secrets, shutil, uuid, zipfile
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_cfg
from app.database import db
from app.models import (
    AdminNotif, Backup, BackupStatus, BackupTrigger, BotText, Domain,
    DomainType, DomainVerify, FREE_PLAN_CODE, GithubLink, GithubSync, AuditLog, PlatformDomain,
    PlatformSetting, ReqStatus, Site, SiteKind, SiteStatus, SubRequest,
    Subscription, SubscriptionPlan, User, UserRole, as_aware,
)

# ═══════════════════════════════════════════════════════════════════
#  EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════
class AppError(Exception):
    def __init__(self, msg: str = "", *, user_msg: str = "حدث خطأ، حاول مجددًا."):
        super().__init__(msg or user_msg)
        self.user_msg = user_msg

class AuthErr(AppError): pass
class BannedErr(AppError):
    def __init__(self, reason=""): super().__init__(user_msg=f"حسابك محظور. السبب: {reason or 'غير محدد'}")
class PermErr(AppError):
    def __init__(self): super().__init__(user_msg="ليس لديك صلاحية لهذا الإجراء.")
class NotFoundErr(AppError):
    def __init__(self, what="المورد"): super().__init__(user_msg=f"{what} غير موجود.")
class LimitErr(AppError): pass
class StorageErr(AppError): pass
class FileErr(AppError): pass
class DomainErr(AppError): pass


# ═══════════════════════════════════════════════════════════════════
#  TEXT ENGINE  (نصوص قابلة للتعديل)
# ═══════════════════════════════════════════════════════════════════
BOT_TEXTS: dict[str, dict] = {
    "welcome":         {"text": "👋 أهلًا بك في <b>SAWHost</b>\n\nمنصة استضافة المواقع الاحترافية للعراقيين والعرب.\n\n🚀 افتح لوحة التحكم لرفع موقعك وإدارته — يصبح مباشراً في دقائق!", "desc": "رسالة الترحيب"},
    "payment_info":    {"text": "💰 <b>طريقة الدفع</b>\n\nتواصل مع المشرف @S_SA_IQ لإتمام الدفع وتفعيل الاشتراك.", "desc": "تعليمات الدفع"},
    "maintenance_msg": {"text": "🛠 المنصة تحت الصيانة حاليًا. حاول لاحقًا.", "desc": "رسالة وضع الصيانة"},
    "about":           {"text": "ℹ️ <b>عن SAWHost</b>\n\nمنصة استضافة مواقع عراقية. للتواصل: @S_SA_IQ", "desc": "صفحة عن المنصة"},
    "platform_domain": {"text": "sawhost-sawhost.hf.space", "desc": "دومين المنصة الافتراضي"},
}

_text_cache: dict[str, str] = {}

async def get_text(key: str) -> str:
    if key in _text_cache: return _text_cache[key]
    async with db() as s:
        r = await s.execute(select(BotText).where(BotText.key == key))
        row = r.scalar_one_or_none()
    val = row.value if row else BOT_TEXTS.get(key, {}).get("text", f"[{key}]")
    _text_cache[key] = val
    return val

async def set_text(key: str, value: str) -> None:
    async with db() as s:
        r = await s.execute(select(BotText).where(BotText.key == key))
        row = r.scalar_one_or_none()
        if row: row.value = value
        else: s.add(BotText(key=key, value=value, desc=BOT_TEXTS.get(key, {}).get("desc", "")))
    _text_cache.pop(key, None)

async def reset_text(key: str) -> None:
    async with db() as s:
        r = await s.execute(select(BotText).where(BotText.key == key))
        row = r.scalar_one_or_none()
        if row: await s.delete(row)
    _text_cache.pop(key, None)

async def get_platform_domain() -> str: return await get_text("platform_domain")


# ═══════════════════════════════════════════════════════════════════
#  PLATFORM SETTINGS
# ═══════════════════════════════════════════════════════════════════
async def get_setting(key: str, default: str = "") -> str:
    async with db() as s:
        r = await s.execute(select(PlatformSetting).where(PlatformSetting.key == key))
        row = r.scalar_one_or_none()
    return row.value if row else default

async def set_setting(key: str, value: str) -> None:
    async with db() as s:
        r = await s.execute(select(PlatformSetting).where(PlatformSetting.key == key))
        row = r.scalar_one_or_none()
        if row: row.value = value
        else: s.add(PlatformSetting(key=key, value=value))

async def is_maintenance() -> bool: return await get_setting("maintenance_mode") == "true"
async def toggle_maintenance(on: bool) -> None: await set_setting("maintenance_mode", "true" if on else "false")

# ميزة ربط الدومينات المخصصة (يملكها المستخدم بنفسه) - معطَّلة افتراضياً
# بطلب صريح من الأدمن، وقابلة للتفعيل لاحقاً من لوحة التحكم في أي وقت.
async def is_custom_domains_enabled() -> bool: return await get_setting("custom_domains_enabled") == "true"
async def toggle_custom_domains(on: bool) -> None: await set_setting("custom_domains_enabled", "true" if on else "false")


# ═══════════════════════════════════════════════════════════════════
#  REFERRAL ENGINE (نظام الإحالة والتجربة المجانية التلقائية بالكامل)
# ═══════════════════════════════════════════════════════════════════
class Referral:
    @staticmethod
    def build_link(bot_username: str, telegram_id: int) -> str:
        return f"https://t.me/{bot_username}?start=ref_{telegram_id}"

    @staticmethod
    async def stats() -> dict:
        """إحصائيات عامة لنظام الإحالة تُعرَض بلوحة الأدمن."""
        async with db() as s:
            now = datetime.now(timezone.utc)
            active_trials = (await s.execute(
                select(func.count(User.id)).where(User.trial_expires_at.is_not(None), User.trial_expires_at > now)
            )).scalar_one()
            total_referrals = (await s.execute(select(func.coalesce(func.sum(User.referral_count), 0)))).scalar_one()
            top_r = await s.execute(
                select(User).where(User.referral_count > 0).order_by(User.referral_count.desc()).limit(10)
            )
            top = list(top_r.scalars().all())
        return {"active_trials": active_trials, "total_referrals": int(total_referrals), "top": top}

    @staticmethod
    async def user_status(uid: uuid.UUID) -> dict:
        async with db() as s:
            r = await s.execute(select(User).where(User.id == uid))
            u = r.scalar_one_or_none()
            if not u: raise NotFoundErr("المستخدم")
            return {
                "is_active": u.is_trial_active,
                "expires_at": u.trial_expires_at,
                "days_total": u.trial_days_total,
                "referral_count": u.referral_count,
            }


# ═══════════════════════════════════════════════════════════════════
#  ADMIN NOTIFICATIONS  (تعمل بدون اتصال صادر - Piggybacking)
# ═══════════════════════════════════════════════════════════════════
async def store_notif(text: str, kind: str = "info") -> None:
    async with db() as s:
        s.add(AdminNotif(text=text, kind=kind))

async def get_unread_notifs() -> list[AdminNotif]:
    async with db() as s:
        r = await s.execute(select(AdminNotif).where(AdminNotif.is_read.is_(False)).order_by(AdminNotif.created_at.desc()).limit(20))
        return list(r.scalars().all())

async def mark_notifs_read() -> None:
    async with db() as s:
        r = await s.execute(select(AdminNotif).where(AdminNotif.is_read.is_(False)))
        for n in r.scalars().all():
            n.is_read = True
            s.add(n)

async def count_unread_notifs() -> int:
    async with db() as s:
        r = await s.execute(select(func.count(AdminNotif.id)).where(AdminNotif.is_read.is_(False)))
        return r.scalar_one()


# ═══════════════════════════════════════════════════════════════════
#  PLANS ENGINE (نظام الخطط الديناميكي - يُدار بالكامل من لوحة الأدمن)
# ═══════════════════════════════════════════════════════════════════
_DEFAULT_SEED_PLANS = [
    # code,      name,             icon, price,  sites, mb,    cdom, pdom, backups, features
    # ملاحظة: "بدون اشتراك" ليست خطة مجانية حقيقية (0 بكل شيء) - هي فقط
    # الحالة الافتراضية التقنية لأي حساب جديد قبل الاشتراك أو الحصول على
    # تجربة عبر الإحالة. الوصول المجاني الوحيد بالمنصة هو عبر نظام
    # الإحالة (Referral) الذي يمنح وقتاً مؤقتاً بأفضل خطة مدفوعة.
    ("free",     "بدون اشتراك",     "🚫", 0,      0,     0,     0,    0,    0,  "لا صلاحيات حتى تشترك بخطة مدفوعة\nأو تحصل على تجربة عبر دعوة الأصدقاء"),
    ("starter",  "Starter",        "🚀", 10_000, 3,     1_000, 1,    1,    3,  "3 مواقع مستضافة\nمساحة 1GB\nدومين مخصص واحد\nدومين منصة واحد\nنسخ احتياطي أسبوعي"),
    ("pro",      "Pro",            "⭐", 25_000, 10,    5_000, 5,    3,    10, "10 مواقع مستضافة\nمساحة 5GB\n5 دومينات مخصصة\n3 دومينات منصة\nنسخ احتياطي يومي\nدعم أولوية"),
    ("business", "Business",       "💎", 50_000, 30,    20_000,20,   10,   30, "30 موقع مستضاف\nمساحة 20GB\n20 دومين مخصص\n10 دومينات منصة\nنسخ احتياطي غير محدود\nدعم مخصص 24/7"),
]

class Plans:
    @staticmethod
    async def seed_defaults() -> None:
        """يُستدعى مرة عند الإقلاع؛ يزرع خططاً افتراضية فقط إن كان الجدول فارغاً بالكامل."""
        async with db() as s:
            cnt = (await s.execute(select(func.count(SubscriptionPlan.id)))).scalar_one()
            if cnt > 0:
                return
            for i, (code, name, icon, price, sites, mb, cdom, pdom, backups, feats) in enumerate(_DEFAULT_SEED_PLANS):
                s.add(SubscriptionPlan(
                    code=code, name=name, icon=icon, price_monthly=price,
                    max_sites=sites, max_mb=mb, max_custom_domains=cdom,
                    max_platform_domains=pdom, max_backups=backups,
                    features=feats,
                    is_active=(code != FREE_PLAN_CODE),  # الخطة الافتراضية مخفية عن قائمة الاشتراك
                    is_default=(code == FREE_PLAN_CODE),
                    sort_order=i,
                ))

    @staticmethod
    async def get_default(session: AsyncSession | None = None) -> SubscriptionPlan:
        async def _q(s):
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.is_default.is_(True)))
            p = r.scalar_one_or_none()
            if p: return p
            r = await s.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order.asc()).limit(1))
            p = r.scalar_one_or_none()
            if not p: raise AppError(user_msg="لا توجد أي خطة اشتراك معرَّفة بالنظام بعد.")
            return p
        if session is not None: return await _q(session)
        async with db() as s: return await _q(s)

    @staticmethod
    async def get_best_paid(session: AsyncSession | None = None) -> SubscriptionPlan | None:
        async def _q(s):
            r = await s.execute(
                select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True), SubscriptionPlan.price_monthly > 0)
                .order_by(SubscriptionPlan.price_monthly.asc()).limit(1)
            )
            return r.scalar_one_or_none()
        if session is not None: return await _q(session)
        async with db() as s: return await _q(s)

    @staticmethod
    async def list_active() -> list[SubscriptionPlan]:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True)).order_by(SubscriptionPlan.sort_order.asc()))
            return list(r.scalars().all())

    @staticmethod
    async def list_all() -> list[SubscriptionPlan]:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order.asc()))
            return list(r.scalars().all())

    @staticmethod
    async def get(pid: uuid.UUID) -> SubscriptionPlan:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == pid))
            p = r.scalar_one_or_none()
            if not p: raise NotFoundErr("الخطة")
            return p

    @staticmethod
    async def get_by_code(code: str) -> SubscriptionPlan:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == code))
            p = r.scalar_one_or_none()
            if not p: raise NotFoundErr("الخطة")
            return p

    @staticmethod
    async def create(code: str, name: str, icon: str, price: int, sites: int, mb: int,
                      cdom: int, pdom: int, backups: int, features: str = "") -> SubscriptionPlan:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == code))
            if r.scalar_one_or_none(): raise AppError(user_msg="يوجد كود خطة مطابق مسبقاً.")
            max_order = (await s.execute(select(func.coalesce(func.max(SubscriptionPlan.sort_order), -1)))).scalar_one()
            p = SubscriptionPlan(code=code, name=name, icon=icon, price_monthly=price,
                                  max_sites=sites, max_mb=mb, max_custom_domains=cdom,
                                  max_platform_domains=pdom, max_backups=backups,
                                  features=features, sort_order=max_order + 1)
            s.add(p); await s.flush(); return p

    @staticmethod
    async def update(pid: uuid.UUID, **fields) -> SubscriptionPlan:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == pid))
            p = r.scalar_one_or_none()
            if not p: raise NotFoundErr("الخطة")
            for k, v in fields.items():
                if hasattr(p, k): setattr(p, k, v)
            s.add(p); return p

    @staticmethod
    async def set_default(pid: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan))
            for p in r.scalars().all():
                p.is_default = (p.id == pid); s.add(p)

    @staticmethod
    async def toggle_active(pid: uuid.UUID) -> SubscriptionPlan:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == pid))
            p = r.scalar_one_or_none()
            if not p: raise NotFoundErr("الخطة")
            if p.is_default: raise AppError(user_msg="لا يمكن إخفاء الخطة الافتراضية.")
            p.is_active = not p.is_active; s.add(p); return p

    @staticmethod
    async def delete(pid: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == pid))
            p = r.scalar_one_or_none()
            if not p: raise NotFoundErr("الخطة")
            if p.is_default: raise AppError(user_msg="لا يمكن حذف الخطة الافتراضية.")
            in_use = (await s.execute(select(func.count(Subscription.id)).where(Subscription.plan_id == pid))).scalar_one()
            if in_use: raise AppError(user_msg=f"لا يمكن الحذف: {in_use} مستخدم مشترك بهذه الخطة حالياً.")
            await s.delete(p)


# ═══════════════════════════════════════════════════════════════════
#  AUTH ENGINE
# ═══════════════════════════════════════════════════════════════════
class Auth:
    @staticmethod
    async def get_or_create(telegram_id: int, username: str|None, first_name: str|None,
                             admin_id: int, referrer_telegram_id: int | None = None) -> tuple[User, bool]:
        """
        referrer_telegram_id: مُمرَّر فقط عند دخول أول مرة عبر رابط إحالة deep-link
        (مثال: t.me/bot?start=ref_123456). يُتجاهل لأي مستخدم موجود مسبقاً.
        """
        async with db() as s:
            r = await s.execute(select(User).where(User.telegram_id == telegram_id))
            user = r.scalar_one_or_none()
            if user:
                changed = False
                if username and user.username != username: user.username = username; changed = True
                if first_name and user.first_name != first_name: user.first_name = first_name; changed = True
                if changed: s.add(user)
                return user, False

            role = UserRole.ADMIN if telegram_id == admin_id else UserRole.USER
            user = User(telegram_id=telegram_id, username=username, first_name=first_name, role=role)

            # ربط المُحيل (فقط عند إنشاء حساب جديد فعلياً، ولا يمكن لمستخدم إحالة نفسه)
            referrer = None
            if referrer_telegram_id and referrer_telegram_id != telegram_id:
                rr = await s.execute(select(User).where(User.telegram_id == referrer_telegram_id))
                referrer = rr.scalar_one_or_none()

            # لا يوجد أي منح تلقائي للتجربة عند الانضمام المجرد بعد الآن.
            # الوصول المجاني الوحيد بالمنصة هو عبر نظام الإحالة: فقط
            # المُحيل (من دعا صديقاً بنجاح) يربح وقت تجربة، لا المستخدم
            # الجديد نفسه لمجرد انضمامه.
            user.trial_days_total = 0
            if referrer:
                user.referred_by_id = referrer.id

            s.add(user)
            await s.flush()

            default_plan = await Plans.get_default(s)
            sub = Subscription(user_id=user.id, plan_id=default_plan.id)
            s.add(sub)

            # مكافأة المُحيل: +1 يوم تجربة (سقف 30 يوم إجمالي)، وتُسجَّل فقط هنا
            # (عند نجاح إنشاء حساب فعلي للمُحال، وليس عند مجرد فتح الرابط)
            if referrer:
                from app.models import TRIAL_REFERRAL_DAYS, TRIAL_MAX_DAYS
                new_total = min(referrer.trial_days_total + TRIAL_REFERRAL_DAYS, TRIAL_MAX_DAYS)
                gained_days = new_total - referrer.trial_days_total
                referrer.trial_days_total = new_total
                referrer.referral_count += 1
                now = datetime.now(timezone.utc)
                ref_exp = as_aware(referrer.trial_expires_at)
                base = ref_exp if (ref_exp and ref_exp > now) else now
                if gained_days > 0:
                    referrer.trial_expires_at = base + timedelta(days=gained_days)
                s.add(referrer)
                await store_notif(
                    f"🎉 إحالة ناجحة! @{referrer.username or referrer.telegram_id} حصل على يوم تجربة إضافي "
                    f"(الإجمالي: {referrer.trial_days_total}/{TRIAL_MAX_DAYS} يوم) — بفضل انضمام @{username or telegram_id}",
                    kind="referral",
                )
            return user, True

    @staticmethod
    async def ban(uid: uuid.UUID, reason: str) -> None:
        async with db() as s:
            r = await s.execute(select(User).where(User.id == uid))
            u = r.scalar_one_or_none()
            if u: u.is_banned = True; u.ban_reason = reason; s.add(u)

    @staticmethod
    async def unban(uid: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(User).where(User.id == uid))
            u = r.scalar_one_or_none()
            if u: u.is_banned = False; u.ban_reason = None; s.add(u)

    @staticmethod
    async def check_owns(user_id: uuid.UUID, owner_id: uuid.UUID) -> None:
        if user_id != owner_id: raise PermErr()


# ═══════════════════════════════════════════════════════════════════
#  STORAGE ENGINE  (حماية قوية من Path Traversal و Zip Slip)
# ═══════════════════════════════════════════════════════════════════
_ALWAYS_FORBIDDEN = {".phar",".exe",".sh",".bash",".bat",".cmd",".ps1",".so",".dll",".jar",".asp",".aspx",".jsp",".cgi",".pl",".pyc",".rb",".htaccess",".htpasswd"}
_APP_EXT = {".php",".phtml",".py"}


def forbidden_ext() -> set[str]:
    """ملفات التطبيقات (.py/.php) تُمنع فقط إن كان تشغيل التطبيقات معطَّلاً على السيرفر؛
    وإلا لا معنى لدعم مواقع Python/PHP ثم رفض ملفاتها بصمت (كان هذا يكسر رفع أي مشروع Python)."""
    from app.process_manager import apps_enabled
    return _ALWAYS_FORBIDDEN if apps_enabled() else (_ALWAYS_FORBIDDEN | _APP_EXT)


class Storage:
    @staticmethod
    def site_root(sid: uuid.UUID) -> Path:
        p = Path(get_cfg().sites_root) / str(sid)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def resolve(sid: uuid.UUID, rel: str) -> Path:
        root = Storage.site_root(sid).resolve()
        clean = rel.strip().replace("\\","/").lstrip("/").replace("\x00","")
        if not clean or clean in (".",".."): return root
        candidate = (root / clean).resolve()
        try: candidate.relative_to(root)
        except ValueError:
            raise FileErr(user_msg="المسار خارج نطاق الموقع — محاولة اختراق مرفوضة.")
        return candidate

    @staticmethod
    def validate_name(name: str) -> None:
        banned = forbidden_ext()
        parts = name.lower().split(".")
        for p in parts[1:]:
            if f".{p}" in banned:
                raise FileErr(user_msg=f"امتداد '.{p}' غير مسموح به.")
        if name.startswith(".ht"): raise FileErr(user_msg="ملفات .ht* غير مسموح بها.")

    @staticmethod
    def ls(sid: uuid.UUID, rel: str = ".") -> list[dict]:
        target = Storage.resolve(sid, rel)
        if not target.is_dir(): return []
        root = Storage.site_root(sid)
        out = []
        for c in sorted(target.iterdir(), key=lambda p:(p.is_file(), p.name.lower())):
            if c.is_symlink(): continue
            try: rp = str(c.relative_to(root))
            except ValueError: continue
            out.append({"name":c.name,"is_dir":c.is_dir(),"rel":rp,"size":c.stat().st_size if c.is_file() else 0})
        return out

    @staticmethod
    async def save(uid: uuid.UUID, sid: uuid.UUID, rel: str, data: bytes) -> Path:
        Storage.validate_name(Path(rel).name)
        mb = len(data)/(1024*1024)
        await Subs.assert_storage(uid, sid, mb)
        target = Storage.resolve(sid, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        await Storage.recalc(sid)
        return target

    @staticmethod
    async def resolve_homepage(sid: uuid.UUID) -> Path | None:
        """
        نظام كشف ذكي ودقيق للصفحة الرئيسية — يعمل وقت الطلب الفعلي (لا
        ينسخ أو يعدّل أي ملف)، فيتكيّف تلقائياً مع أي تغيير بالملفات دون
        الحاجة لأي معالجة مسبقة. الترتيب:
          1. أسماء الصفحة الرئيسية القياسية (index.html/.htm) بأي حالة أحرف.
          2. أسماء شائعة بديلة (home/main/default/start/welcome.html).
          3. لو وُجد ملف HTML واحد بالجذر فقط، يُستخدم مباشرة (مهما كان اسمه).
          4. لو وُجدت عدة ملفات HTML بلا اسم قياسي، يُختار الأقصر اسماً ثم
             أبجدياً (قرار حتمي ثابت دائماً، وليس عشوائياً أو متغيّراً).
          5. لا يوجد أي ملف HTML بالجذر إطلاقاً → None (تظهر عندها صفحة
             تشخيصية واضحة بدل تخمين خاطئ).
        """
        root = Storage.site_root(sid)
        try:
            entries = [p for p in root.iterdir() if p.is_file()]
        except Exception:
            return None

        exact = {"index.html", "index.htm"}
        common = {"home.html", "main.html", "default.html", "start.html", "welcome.html"}

        for p in entries:
            if p.name.lower() in exact:
                return p
        for p in entries:
            if p.name.lower() in common:
                return p

        html_files = [p for p in entries if p.suffix.lower() in (".html", ".htm")]
        if len(html_files) == 1:
            return html_files[0]
        if len(html_files) > 1:
            html_files.sort(key=lambda p: (len(p.name), p.name.lower()))
            return html_files[0]
        return None
        return False

    MAX_FILE_BYTES = 100 * 1024 * 1024   # سقف الملف الواحد داخل أي أرشيف
    MAX_ARCHIVE_FILES = 10_000

    @staticmethod
    def _clean_member(name: str) -> str | None:
        """يرجع مساراً نسبياً نظيفاً أو None إن كان خطيراً/فارغاً."""
        safe = name.replace("\\", "/").replace("\x00", "").lstrip("/")
        parts = [p for p in safe.split("/") if p not in ("", ".")]
        if not parts or ".." in parts: return None
        if any(p.startswith(".git") and p in (".git",) for p in parts): return None
        return "/".join(parts)

    @staticmethod
    def _write_bounded(dst: Path, src, limit: int) -> int:
        """يكتب من ملف مفتوح بحدّ أقصى صارم للحجم (حماية من zip/tar bombs)."""
        n = 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".sw-part")
        try:
            with open(tmp, "wb") as f:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk: break
                    n += len(chunk)
                    if n > limit: raise FileErr(user_msg="ملف داخل الأرشيف يتجاوز الحد المسموح (100MB).")
                    f.write(chunk)
            if dst.is_symlink() or (dst.exists() and dst.is_dir()):
                raise FileErr(user_msg="تعارض مسار داخل الأرشيف.")
            tmp.replace(dst)
        finally:
            tmp.unlink(missing_ok=True)
        return n

    @staticmethod
    def _check_target(dest_r: Path, dest: Path, safe: str) -> Path | None:
        """مسار الكتابة النهائي، ويُرفض لو مرّ عبر رابط رمزي أو خرج من الجذر."""
        mp = dest / safe
        cur = dest
        for part in safe.split("/")[:-1]:
            cur = cur / part
            if cur.is_symlink(): return None
        if mp.is_symlink(): return None
        try: mp.resolve().relative_to(dest_r)
        except ValueError: return None
        return mp

    @staticmethod
    async def extract_zip(uid: uuid.UUID, sid: uuid.UUID, data: bytes, rel: str = ".") -> int:
        dest = Storage.resolve(sid, rel); dest.mkdir(parents=True, exist_ok=True)
        dest_r = dest.resolve()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = z.infolist()
                if len(infos) > Storage.MAX_ARCHIVE_FILES: raise FileErr(user_msg="ZIP يحتوي أكثر من 10000 ملف.")
                total_mb = sum(e.file_size for e in infos) / (1024*1024)
                await Subs.assert_storage(uid, sid, total_mb)
                count = 0
                for m in infos:
                    if (m.external_attr >> 16) & 0o170000 == 0o120000: continue   # رابط رمزي: يُتجاهَل
                    safe = Storage._clean_member(m.filename)
                    if not safe: continue
                    if m.is_dir():
                        mp = Storage._check_target(dest_r, dest, safe + "/x")
                        if mp: mp.parent.mkdir(parents=True, exist_ok=True)
                        continue
                    try: Storage.validate_name(Path(safe).name)
                    except FileErr: continue
                    mp = Storage._check_target(dest_r, dest, safe)
                    if mp is None: continue
                    with z.open(m) as src:
                        Storage._write_bounded(mp, src, Storage.MAX_FILE_BYTES)
                    count += 1
        except zipfile.BadZipFile: raise FileErr(user_msg="ملف ZIP تالف.")
        await Storage.recalc(sid)
        return count

    @staticmethod
    async def install_tarball(uid: uuid.UUID, sid: uuid.UUID, data: bytes, subdir: str | None = None,
                              old_manifest: list[str] | None = None) -> tuple[int, int, list[str]]:
        """
        ينشر أرشيف GitHub (tar.gz) في جذر الموقع بنفس معايير الأمان:
        تجاهل الروابط الرمزية/الأجهزة، سقف للملف والعدد والمساحة، رفض أي مسار يمرّ
        عبر رابط رمزي. يحذف الملفات التي كانت منشورة سابقاً (manifest) واختفت من المستودع،
        دون لمس ملفات رفعها المستخدم يدوياً.
        يرجع (عدد الملفات، عدد المتجاهَل، manifest الجديد).
        """
        import tarfile
        dest = Storage.site_root(sid); dest_r = dest.resolve()
        sub = Storage._clean_member(subdir or "") if subdir else None
        written: list[str] = []; skipped = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                members = tf.getmembers()
                if len(members) > Storage.MAX_ARCHIVE_FILES: raise FileErr(user_msg="المستودع يحتوي أكثر من 10000 ملف.")
                prefix = None
                for m in members:
                    if "/" in m.name: prefix = m.name.split("/", 1)[0] + "/"; break
                files = []
                for m in members:
                    if not m.isfile(): continue
                    name = m.name[len(prefix):] if prefix and m.name.startswith(prefix) else m.name
                    safe = Storage._clean_member(name)
                    if not safe: continue
                    if sub:
                        if not (safe == sub or safe.startswith(sub + "/")): continue
                        safe = safe[len(sub):].lstrip("/")
                        if not safe: continue
                    files.append((m, safe))
                await Subs.assert_storage(uid, sid, sum(m.size for m, _ in files) / (1024*1024))
                for m, safe in files:
                    try: Storage.validate_name(Path(safe).name)
                    except FileErr: skipped += 1; continue
                    if m.size > Storage.MAX_FILE_BYTES: skipped += 1; continue
                    mp = Storage._check_target(dest_r, dest, safe)
                    if mp is None: skipped += 1; continue
                    src = tf.extractfile(m)
                    if src is None: skipped += 1; continue
                    Storage._write_bounded(mp, src, Storage.MAX_FILE_BYTES)
                    written.append(safe)
        except tarfile.TarError:
            raise FileErr(user_msg="أرشيف المستودع تالف أو بصيغة غير مدعومة.")
        # حذف ما اختفى من المستودع (من الملفات التي نشرناها نحن فقط)
        for old in (old_manifest or []):
            if old in written: continue
            clean = Storage._clean_member(old)
            if not clean: continue
            mp = Storage._check_target(dest_r, dest, clean)
            if mp is not None and mp.is_file():
                mp.unlink(missing_ok=True)
                par = mp.parent
                while par != dest_r:
                    try: par.rmdir()
                    except OSError: break
                    par = par.parent
        await Storage.recalc(sid)
        return len(written), skipped, written

    @staticmethod
    def delete(sid: uuid.UUID, rel: str) -> None:
        t = Storage.resolve(sid, rel)
        if not t.exists(): raise FileErr(user_msg="المسار غير موجود.")
        if t == Storage.site_root(sid): raise FileErr(user_msg="لا يمكن حذف المجلد الجذري.")
        shutil.rmtree(t) if t.is_dir() else t.unlink()

    @staticmethod
    def mkdir(sid: uuid.UUID, rel: str) -> None:
        Storage.resolve(sid, rel).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def rename(sid: uuid.UUID, rel: str, new: str) -> None:
        new = new.replace("/","").replace("\\","").replace("\x00","").strip()
        if not new or new in (".",".."): raise FileErr(user_msg="اسم غير صالح.")
        s = Storage.resolve(sid, rel)
        if not s.exists(): raise FileErr(user_msg="المسار غير موجود.")
        d = s.parent / new
        if d.exists(): raise FileErr(user_msg="الاسم مستخدم بالفعل.")
        s.rename(d)

    @staticmethod
    def compress(sid: uuid.UUID, rel: str = ".") -> bytes:
        t = Storage.resolve(sid, rel)
        if not t.exists(): raise FileErr(user_msg="المسار غير موجود.")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            if t.is_file() and not t.is_symlink(): z.write(t, t.name)
            else:
                for f in sorted(t.rglob("*")):
                    if f.is_symlink(): continue   # لا نُصدِّر روابط رمزية (تسريب ملفات الخادم)
                    if f.is_file(): z.write(f, str(f.relative_to(t)))
        buf.seek(0); return buf.read()

    @staticmethod
    async def recalc(sid: uuid.UUID) -> int:
        root = Storage.site_root(sid)
        total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink())
        mb = max(1, total//(1024*1024)) if total else 0
        async with db() as s:
            r = await s.execute(select(Site).where(Site.id == sid))
            site = r.scalar_one_or_none()
            if site: site.used_mb = mb; s.add(site)
        return mb

    @staticmethod
    def remove_site(sid: uuid.UUID) -> None:
        root = Storage.site_root(sid)
        if root.exists(): shutil.rmtree(root)


# ═══════════════════════════════════════════════════════════════════
#  SUBSCRIPTION ENGINE
# ═══════════════════════════════════════════════════════════════════
class Subs:
    @staticmethod
    async def get(uid: uuid.UUID) -> Subscription:
        async with db() as s:
            r = await s.execute(
                select(Subscription).options(selectinload(Subscription.plan)).where(Subscription.user_id == uid)
            )
            sub = r.scalar_one_or_none()
            if not sub: raise NotFoundErr("الاشتراك")
            # انتهاء الاشتراك: نُنزِل الحدود فعلياً للخطة الافتراضية (بدون اشتراك)
            exp = as_aware(sub.expires_at)
            if exp and exp < datetime.now(timezone.utc):
                sub._downgrade_plan = await Plans.get_default(s)
            # طبقة التجربة: إن كانت نشطة، نمنح مؤقتاً حدود أفضل خطة مدفوعة متاحة
            # (فقط إن كانت أعلى من خطته الحالية) دون تعديل الاشتراك الحقيقي إطلاقاً
            ur = await s.execute(select(User).where(User.id == uid))
            u = ur.scalar_one_or_none()
            if u and u.is_trial_active:
                best = await Plans.get_best_paid(s)
                if best and best.price_monthly > 0:
                    sub._trial_plan = best
            return sub

    @staticmethod
    async def has_access(uid: uuid.UUID) -> bool:
        """هل يملك المستخدم اشتراكاً فعّالاً (أو تجربة)؟ الأدمن دائماً نعم."""
        async with db() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if not u: return False
        if u.is_admin: return True
        return (await Subs.get(uid)).max_sites > 0

    @staticmethod
    async def assert_active(uid: uuid.UUID) -> None:
        if not await Subs.has_access(uid):
            raise LimitErr(user_msg="اشتراكك غير فعّال أو منتهٍ. اشترك أو جدّد اشتراكك لتتمكن من التشغيل والتعديل.")

    @staticmethod
    async def _is_admin(uid: uuid.UUID) -> bool:
        async with db() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        return bool(u and u.is_admin)

    @staticmethod
    async def assert_sites(uid: uuid.UUID) -> None:
        if await Subs._is_admin(uid): return
        sub = await Subs.get(uid)
        async with db() as s:
            cnt_r = await s.execute(select(func.count(Site.id)).where(Site.owner_id == uid, Site.is_deleted.is_(False)))
            cnt = cnt_r.scalar_one()
        if cnt >= sub.max_sites:
            raise LimitErr(user_msg=f"وصلت للحد الأقصى ({sub.max_sites} مواقع). رقّي اشتراكك.")

    @staticmethod
    async def assert_storage(uid: uuid.UUID, sid: uuid.UUID, extra_mb: float) -> None:
        if await Subs._is_admin(uid): return
        sub = await Subs.get(uid)
        async with db() as s:
            tot_r = await s.execute(select(func.coalesce(func.sum(Site.used_mb),0)).where(Site.owner_id == uid, Site.is_deleted.is_(False)))
            tot = tot_r.scalar_one()
        if (tot + extra_mb) > sub.max_mb:
            raise LimitErr(user_msg=f"تجاوزت المساحة ({sub.max_mb}MB). احذف ملفات أو رقّي اشتراكك.")

    @staticmethod
    async def assert_domains(uid: uuid.UUID) -> None:
        if await Subs._is_admin(uid): return
        sub = await Subs.get(uid)
        async with db() as s:
            cnt_r = await s.execute(select(func.count(Domain.id)).join(Site,Site.id==Domain.site_id).where(Site.owner_id==uid, Domain.dtype==DomainType.CUSTOM))
            cnt = cnt_r.scalar_one()
        if cnt >= sub.max_domains:
            raise LimitErr(user_msg=f"وصلت لحد الدومينات المخصصة ({sub.max_domains}). رقّي اشتراكك.")

    @staticmethod
    async def assert_platform_domains(uid: uuid.UUID) -> None:
        if await Subs._is_admin(uid): return
        sub = await Subs.get(uid)
        async with db() as s:
            cnt_r = await s.execute(select(func.count(Domain.id)).join(Site,Site.id==Domain.site_id).where(Site.owner_id==uid, Domain.dtype==DomainType.PLATFORM))
            cnt = cnt_r.scalar_one()
        if cnt >= sub.max_platform_domains:
            raise LimitErr(user_msg=f"وصلت لحد دومينات المنصة المتاحة لخطتك ({sub.max_platform_domains}). رقّي اشتراكك.")

    @staticmethod
    async def assert_backups(uid: uuid.UUID, sid: uuid.UUID) -> None:
        if await Subs._is_admin(uid): return
        sub = await Subs.get(uid)
        async with db() as s:
            cnt_r = await s.execute(select(func.count(Backup.id)).where(Backup.site_id == sid))
            cnt = cnt_r.scalar_one()
        if cnt >= sub.max_backups:
            raise LimitErr(user_msg=f"وصلت لحد النسخ ({sub.max_backups}/موقع). رقّي اشتراكك.")

    @staticmethod
    async def change_plan(uid: uuid.UUID, plan_id: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(Subscription).where(Subscription.user_id == uid))
            sub = r.scalar_one_or_none()
            if sub: sub.plan_id = plan_id; s.add(sub)

    @staticmethod
    async def is_expired(sub: Subscription) -> bool:
        exp = as_aware(sub.expires_at)
        if not exp: return False
        return exp < datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════
#  HOSTING ENGINE
# ═══════════════════════════════════════════════════════════════════
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")

def _safe_kind(kind: str) -> SiteKind:
    """يحوّل نوع process_manager إلى SiteKind؛ أي قيمة غير معروفة تُعامل كموقع ثابت بدل أن ينهار الإنشاء."""
    try:
        return SiteKind(kind)
    except ValueError:
        return SiteKind.STATIC


_RESERVED = {"www","api","admin","app","sawhost","dashboard","mail","ftp","status","dl","panel","static","login","webhook","gh","s","support","help","root","ns1","ns2","smtp","cdn"}


def _mk_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    slug = slug[:63] or "site"
    # أسماء محجوزة (منعاً لانتحال صفة المنصة: admin.xxx / www.xxx) وأقل من 3 أحرف
    if slug in _RESERVED or len(slug) < 3:
        slug = f"{slug}-site"
    return slug

class Host:
    @staticmethod
    def validate_slug(slug: str) -> str:
        s = slug.strip().lower()
        if not _SLUG_RE.match(s): raise AppError(user_msg="الرابط: أحرف إنجليزية صغيرة وأرقام وشرطات (3-63 حرفًا).")
        if s in _RESERVED: raise AppError(user_msg="هذا الاسم محجوز.")
        return s

    @staticmethod
    async def create(uid: uuid.UUID, name: str, zip_bytes: bytes | None = None) -> Site:
        await Subs.assert_sites(uid)
        slug = _mk_slug(name)
        # تأكد من عدم تكرار الـ slug
        async with db() as s:
            for attempt in range(10):
                candidate = slug if attempt == 0 else f"{slug}-{attempt}"
                r = await s.execute(select(Site).where(Site.slug == candidate))
                if not r.scalar_one_or_none(): slug = candidate; break
            site = Site(owner_id=uid, slug=slug, name=name.strip() or slug, status=SiteStatus.STOPPED)
            s.add(site); await s.flush(); sid = site.id

        # ملفات أولية
        try:
            site_root = Storage.site_root(sid)
            if zip_bytes:
                await Storage.extract_zip(uid, sid, zip_bytes)
                # لا حاجة لأي نسخ/تصحيح هنا بعد الآن - نظام Storage.resolve_homepage
                # يكتشف الصفحة الرئيسية ديناميكياً وقت كل طلب زيارة، أياً كان اسمها.
            else:
                default_html = f'<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>{_h.escape(name)}</title><style>body{{font-family:system-ui;background:#0f172a;color:#f1f5f9;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}.c{{text-align:center}}</style></head><body><div class="c"><h1>🚀 {_h.escape(name)}</h1><p>ارفع ملفات موقعك عبر مدير الملفات</p></div></body></html>'
                await Storage.save(uid, sid, "index.html", default_html.encode())
        except Exception:
            # فشل الرفع/فك الضغط (مثلاً تجاوز الحد): نحذف الموقع الناقص بدل تركه يتيماً
            async with db() as s:
                r = await s.execute(select(Site).where(Site.id == sid))
                orphan = r.scalar_one_or_none()
                if orphan: orphan.is_deleted = True; s.add(orphan)
            Storage.remove_site(sid)
            raise

        # كشف نوع المشروع تلقائياً (static / python / node / php) من محتويات الملفات
        from app.process_manager import detect_kind
        kind = detect_kind(site_root)
        async with db() as s:
            r = await s.execute(select(Site).where(Site.id == sid))
            site = r.scalar_one_or_none()
            if site: site.kind = _safe_kind(kind); s.add(site)

        # ملاحظة هندسية مهمة: لا نُنشئ أي دومين فرعي (Subdomain) تلقائي هنا.
        # HuggingFace Spaces لا يدعم Wildcard DNS للدومينات الفرعية على
        # الإطلاق (فقط الدومين الأساسي المُعطى للـ Space نفسه يعمل)، فأي
        # رابط بصيغة {slug}.{platform} سيُرجع 404 دائماً من HuggingFace
        # نفسها قبل حتى الوصول لكودنا. الرابط الرسمي الوحيد المضمون هو
        # التوجيه بالمسار: {base_url}/s/{slug} (يُبنى مباشرة من site.slug
        # دون حاجة لأي سجل Domain بقاعدة البيانات).

        return await Host.get(sid)

    @staticmethod
    async def redetect_kind(sid: uuid.UUID) -> SiteKind:
        """يُستدعى بعد أي رفع/تعديل ملفات لإعادة كشف نوع المشروع (مثلاً لو أضاف المستخدم requirements.txt لاحقاً)."""
        from app.process_manager import detect_kind
        site_root = Storage.site_root(sid)
        kind = detect_kind(site_root)
        async with db() as s:
            r = await s.execute(select(Site).where(Site.id == sid))
            site = r.scalar_one_or_none()
            if not site: raise NotFoundErr("الموقع")
            site.kind = _safe_kind(kind); s.add(site)
            return site.kind

    @staticmethod
    async def get(sid: uuid.UUID) -> Site:
        async with db() as s:
            r = await s.execute(select(Site).where(Site.id == sid, Site.is_deleted.is_(False)))
            site = r.scalar_one_or_none()
            if not site: raise NotFoundErr("الموقع")
            return site

    @staticmethod
    async def list_user(uid: uuid.UUID) -> list[Site]:
        async with db() as s:
            r = await s.execute(select(Site).where(Site.owner_id==uid, Site.is_deleted.is_(False)).order_by(Site.created_at.desc()))
            return list(r.scalars().all())

    @staticmethod
    async def _set_status(sid: uuid.UUID, status: SiteStatus, error: str | None = None) -> Site:
        async with db() as s:
            r = await s.execute(select(Site).where(Site.id == sid))
            site = r.scalar_one_or_none()
            if not site: raise NotFoundErr("الموقع")
            site.status = status
            site.last_error = error
            s.add(site); return site

    @staticmethod
    async def start(sid: uuid.UUID) -> Site:
        """
        غير محجوب بالكامل: للمواقع الثابتة تتغير الحالة فوراً. لتطبيقات
        Python/Node/PHP، تُطلَق عملية التشغيل بالخلفية (قد تستغرق حتى
        دقيقتين لتثبيت المتطلبات لأول مرة) وتعود هذه الدالة فوراً دون أي
        انتظار، بحالة DEPLOYING، ليبقى البوت سلساً بلا أي تجمّد على الزر.
        استخدم Host.check_deploy(sid) لاحقاً لمعرفة نتيجة النشر الفعلية.
        """
        site = await Host.get(sid)
        await Subs.assert_active(site.owner_id)
        if site.kind == SiteKind.STATIC:
            return await Host._set_status(sid, SiteStatus.RUNNING)

        import app.process_manager as pm
        site_root = Storage.site_root(sid)
        result = pm.trigger_start(str(sid), site_root, site.kind.value)
        if result == "disabled":
            return await Host._set_status(sid, SiteStatus.ERROR, error=pm.APPS_DISABLED_MSG)
        if result == "running":
            port = pm.get_port(str(sid))
            async with db() as s:
                r = await s.execute(select(Site).where(Site.id == sid))
                s_obj = r.scalar_one_or_none()
                if s_obj:
                    s_obj.status = SiteStatus.RUNNING; s_obj.proc_port = port; s_obj.last_error = None
                    s.add(s_obj)
            return await Host.get(sid)
        # 'started' أو 'already_deploying': النشر جارٍ بالخلفية، لا ننتظر شيئاً هنا
        return await Host._set_status(sid, SiteStatus.DEPLOYING)

    @staticmethod
    async def check_deploy(sid: uuid.UUID) -> dict:
        """
        استطلاع فوري (بلا أي انتظار) لحالة نشر تطبيق Python/Node/PHP بعد
        الضغط على تشغيل. يُزامن حالة قاعدة البيانات مع نتيجة process_manager
        الفعلية عند اكتمال النشر (نجاحاً أو فشلاً).
        """
        site = await Host.get(sid)
        if site.kind == SiteKind.STATIC:
            return {"state": "running" if site.status == SiteStatus.RUNNING else "idle", "message": None}
        import app.process_manager as pm
        status = pm.get_status(str(sid))
        if status["state"] == "running":
            port = pm.get_port(str(sid))
            async with db() as s:
                r = await s.execute(select(Site).where(Site.id == sid))
                s_obj = r.scalar_one_or_none()
                if s_obj and s_obj.status != SiteStatus.RUNNING:
                    s_obj.status = SiteStatus.RUNNING; s_obj.proc_port = port; s_obj.last_error = None
                    s.add(s_obj)
        elif status["state"] == "error":
            await Host._set_status(sid, SiteStatus.ERROR, error=status.get("message"))
        return status

    @staticmethod
    async def stop(sid: uuid.UUID) -> Site:
        site = await Host.get(sid)
        if site.kind != SiteKind.STATIC:
            import app.process_manager as pm
            await pm.stop_process(str(sid), Storage.site_root(sid))
        return await Host._set_status(sid, SiteStatus.STOPPED)

    @staticmethod
    async def restart(sid: uuid.UUID) -> Site:
        await Subs.assert_active((await Host.get(sid)).owner_id)
        await Host.stop(sid)
        return await Host.start(sid)

    @staticmethod
    async def rename(sid: uuid.UUID, new_name: str) -> Site:
        async with db() as s:
            r = await s.execute(select(Site).where(Site.id == sid))
            site = r.scalar_one_or_none()
            if not site: raise NotFoundErr("الموقع")
            site.name = new_name.strip(); s.add(site); return site

    @staticmethod
    async def delete(sid: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(Site).where(Site.id == sid))
            site = r.scalar_one_or_none()
            if not site: return
            site.is_deleted = True; site.status = SiteStatus.STOPPED; s.add(site)
        Storage.remove_site(sid)


# ═══════════════════════════════════════════════════════════════════
#  GITHUB INTEGRATION ENGINE
# ═══════════════════════════════════════════════════════════════════
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,150}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_GH_API = "https://api.github.com"
_GH_MAX_TARBALL = 120 * 1024 * 1024
_gh_locks: dict[uuid.UUID, Any] = {}


class GithubError(AppError):
    pass


class GithubIntegration:
    """
    تكامل GitHub الحقيقي: فحص المستودع والفروع، مستودعات خاصة بتوكن مشفَّر،
    مزامنة بـ SHA فعلي، مجلد فرعي اختياري، حذف الملفات المحذوفة من المستودع،
    سجل مزامنات، وإنشاء/حذف Webhook تلقائياً.
    """

    # ── أدوات الاتصال ─────────────────────────────────────────────
    @staticmethod
    def _headers(token: str | None) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "SawHost/2.0"}
        if token: h["Authorization"] = f"Bearer {token}"
        return h

    @staticmethod
    def _platform_token() -> str | None:
        import os
        return os.getenv("GITHUB_TOKEN") or None

    @staticmethod
    async def _request(method: str, path: str, token: str | None, *, json_body: dict | None = None,
                       want_bytes: bool = False, limit: int = 0):
        import aiohttp
        url = path if path.startswith("http") else _GH_API + path
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60 if want_bytes else 20)) as sess:
                async with sess.request(method, url, headers=GithubIntegration._headers(token), json=json_body) as resp:
                    if resp.status >= 400:
                        try: msg = (await resp.json()).get("message", "")
                        except Exception: msg = ""
                        remaining = resp.headers.get("X-RateLimit-Remaining")
                        if resp.status in (401,):
                            raise GithubError(user_msg="توكن GitHub غير صالح أو منتهي.")
                        if resp.status == 403 and (remaining == "0" or "rate limit" in msg.lower()):
                            raise GithubError(user_msg="تجاوزتَ حد طلبات GitHub مؤقتاً. أضف توكناً شخصياً أو حاول لاحقاً.")
                        if resp.status in (403,):
                            raise GithubError(user_msg="GitHub رفض الطلب: صلاحيات التوكن غير كافية.")
                        if resp.status == 404:
                            raise GithubError(user_msg="غير موجود على GitHub. إن كان المستودع خاصاً فأضف توكناً بصلاحية القراءة.")
                        raise GithubError(user_msg=f"GitHub أعاد خطأ HTTP {resp.status}. {msg[:120]}")
                    if want_bytes:
                        buf = bytearray()
                        async for chunk in resp.content.iter_chunked(1 << 20):
                            buf += chunk
                            if limit and len(buf) > limit:
                                raise GithubError(user_msg="حجم المستودع يتجاوز الحد المسموح (120MB).")
                        return bytes(buf)
                    if resp.status == 204: return {}
                    return await resp.json()
        except GithubError:
            raise
        except Exception as e:
            raise GithubError(user_msg="⚠️ تعذّر الاتصال بـ GitHub من السيرفر (قد تحظر بيئة التشغيل الاتصال الصادر أو انتهت المهلة).") from e

    @staticmethod
    def _token_of(link: GithubLink | None) -> str | None:
        if not link or not link.token_enc: return None
        from app.security import unseal
        return unseal(link.token_enc)

    # ── تحليل المدخلات ────────────────────────────────────────────
    @staticmethod
    def parse_repo(text: str) -> tuple[str, str]:
        """يقبل owner/repo أو رابط github.com كامل، ويرجع (owner, repo)."""
        t = text.strip()
        t = re.sub(r"^https?://(www\.)?github\.com/", "", t, flags=re.IGNORECASE)
        t = re.split(r"[?#]", t)[0]
        t = t.removesuffix(".git").strip("/")
        t = "/".join(t.split("/")[:2])
        if not _GITHUB_REPO_RE.match(t) or t.startswith(".") or "/." in t:
            raise AppError(user_msg="صيغة غير صحيحة. أرسل باسم owner/repo أو رابط GitHub كامل.")
        owner, repo = t.split("/", 1)
        return owner, repo

    @staticmethod
    def check_branch(branch: str) -> str:
        b = (branch or "main").strip()
        if not _BRANCH_RE.match(b) or ".." in b or b.startswith("/") or b.endswith("/"):
            raise AppError(user_msg="اسم الفرع غير صالح.")
        return b

    @staticmethod
    def check_subdir(sub: str | None) -> str | None:
        if not sub: return None
        c = Storage._clean_member(sub)
        if not c or len(c) > 200 or not re.match(r"^[\w.\-/ ]+$", c, re.UNICODE):
            raise AppError(user_msg="المجلد الفرعي غير صالح.")
        return c

    # ── فحص المستودع ──────────────────────────────────────────────
    @staticmethod
    async def inspect(owner: str, repo: str, token: str | None = None) -> dict:
        """يتحقق من المستودع ويرجع بياناته وفروعه. بلا توكن يُرفض الخاص."""
        use = token or GithubIntegration._platform_token()
        info = await GithubIntegration._request("GET", f"/repos/{owner}/{repo}", use)
        if info.get("private") and not token:
            raise GithubError(user_msg="هذا المستودع خاص. أضف توكن GitHub شخصياً (Fine-grained، صلاحية Contents: Read).")
        branches = []
        try:
            br = await GithubIntegration._request("GET", f"/repos/{owner}/{repo}/branches?per_page=100", use)
            branches = [b["name"] for b in br if isinstance(b, dict) and "name" in b]
        except GithubError:
            pass
        return {
            "full_name": info.get("full_name"), "private": bool(info.get("private")),
            "description": (info.get("description") or "")[:200], "default_branch": info.get("default_branch") or "main",
            "stars": info.get("stargazers_count", 0), "language": info.get("language"),
            "size_kb": info.get("size", 0), "pushed_at": info.get("pushed_at"),
            "avatar": (info.get("owner") or {}).get("avatar_url"), "branches": branches,
        }

    # ── الربط ─────────────────────────────────────────────────────
    @staticmethod
    async def link(uid: uuid.UUID, sid: uuid.UUID, owner: str, repo: str, branch: str = "main",
                   token: str | None = None, subdir: str | None = None, keep_token: bool = True,
                   clean: bool = True) -> GithubLink:
        subdir = GithubIntegration.check_subdir(subdir)
        existing = await GithubIntegration.get(sid)
        eff_token = token or (GithubIntegration._token_of(existing) if (existing and keep_token) else None)
        info = await GithubIntegration.inspect(owner, repo, eff_token)   # يفشل بوضوح إن لم يكن متاحاً
        branch = GithubIntegration.check_branch((branch or "").strip() or info.get("default_branch") or "main")
        from app.security import seal
        async with db() as s:
            r = await s.execute(select(GithubLink).where(GithubLink.site_id == sid))
            l = r.scalar_one_or_none()
            if l:
                if (l.repo_owner, l.repo_name) != (owner, repo):
                    l.last_sha = None; l.manifest = None; l.webhook_id = None
                l.repo_owner, l.repo_name, l.branch, l.subdir = owner, repo, branch, subdir
                l.last_sync_error = None
            else:
                l = GithubLink(site_id=sid, repo_owner=owner, repo_name=repo, branch=branch, subdir=subdir,
                               webhook_secret=secrets.token_hex(24))
            l.clean = bool(clean); l.is_private = bool(info.get("private"))
            if token: l.token_enc = seal(token)
            elif not keep_token or not eff_token: l.token_enc = None
            s.add(l); await s.flush()
            return l

    @staticmethod
    async def get(sid: uuid.UUID) -> GithubLink | None:
        async with db() as s:
            r = await s.execute(select(GithubLink).where(GithubLink.site_id == sid))
            return r.scalar_one_or_none()

    @staticmethod
    async def unlink(sid: uuid.UUID) -> None:
        link = await GithubIntegration.get(sid)
        if link and link.webhook_id:
            try: await GithubIntegration.delete_webhook(link)
            except Exception: pass
        async with db() as s:
            r = await s.execute(select(GithubLink).where(GithubLink.site_id == sid))
            l = r.scalar_one_or_none()
            if l: await s.delete(l)

    @staticmethod
    async def toggle_auto_sync(sid: uuid.UUID) -> GithubLink:
        async with db() as s:
            r = await s.execute(select(GithubLink).where(GithubLink.site_id == sid))
            link = r.scalar_one_or_none()
            if not link: raise NotFoundErr("ربط GitHub")
            link.auto_sync = not link.auto_sync
            s.add(link); return link

    # ── Webhook على GitHub ────────────────────────────────────────
    @staticmethod
    async def create_webhook(link: GithubLink) -> int:
        token = GithubIntegration._token_of(link)
        if not token:
            raise GithubError(user_msg="إنشاء الـ Webhook تلقائياً يحتاج توكناً بصلاحية Webhooks: Read and write.")
        base = get_cfg().public_url
        if not base.startswith("https://"):
            raise GithubError(user_msg="WEBHOOK_BASE_URL يجب أن يكون https لإنشاء Webhook.")
        body = {"name": "web", "active": True, "events": ["push"], "config": {
            "url": f"{base}/gh/{link.site_id}", "content_type": "json",
            "secret": link.webhook_secret, "insecure_ssl": "0"}}
        res = await GithubIntegration._request("POST", f"/repos/{link.repo_owner}/{link.repo_name}/hooks", token, json_body=body)
        hid = int(res.get("id", 0)) or None
        async with db() as s:
            r = await s.execute(select(GithubLink).where(GithubLink.id == link.id))
            l = r.scalar_one_or_none()
            if l: l.webhook_id = hid; l.auto_sync = True; s.add(l)
        return hid or 0

    @staticmethod
    async def delete_webhook(link: GithubLink) -> None:
        token = GithubIntegration._token_of(link)
        if not (token and link.webhook_id): return
        await GithubIntegration._request("DELETE", f"/repos/{link.repo_owner}/{link.repo_name}/hooks/{link.webhook_id}", token)

    # ── المزامنة ──────────────────────────────────────────────────
    @staticmethod
    async def _record(sid: uuid.UUID, *, ok: bool, trigger: str, sha: str | None, message: str | None,
                      files: int = 0, detail: str | None = None) -> None:
        async with db() as s:
            s.add(GithubSync(site_id=sid, sha=sha, message=(message or "")[:300], trigger=trigger, ok=ok,
                             files=files, detail=(detail or "")[:1000]))

    @staticmethod
    async def history(sid: uuid.UUID, limit: int = 15) -> list[GithubSync]:
        async with db() as s:
            r = await s.execute(select(GithubSync).where(GithubSync.site_id == sid)
                                .order_by(GithubSync.created_at.desc()).limit(limit))
            return list(r.scalars().all())

    @staticmethod
    async def sync_now(sid: uuid.UUID, uid: uuid.UUID, trigger: str = "manual", force: bool = False) -> str:
        """يسحب آخر commit من الفرع المربوط وينشره. قفل لكل موقع يمنع تزامنين متوازيين."""
        import asyncio
        lock = _gh_locks.setdefault(sid, asyncio.Lock())
        if lock.locked():
            raise AppError(user_msg="هناك مزامنة جارية لهذا الموقع. انتظر حتى تنتهي.")
        async with lock:
            link = await GithubIntegration.get(sid)
            if not link: raise AppError(user_msg="لا يوجد ربط GitHub لهذا الموقع.")
            user_token = GithubIntegration._token_of(link)
            token = user_token or GithubIntegration._platform_token()
            sha = msg = None
            try:
                if not user_token:  # لا نستخدم توكن المنصة إلا لمستودع ثبت أنه عام
                    info = await GithubIntegration._request("GET", f"/repos/{link.repo_owner}/{link.repo_name}", token)
                    if info.get("private"):
                        raise GithubError(user_msg="المستودع أصبح خاصاً. أضف توكن GitHub لمتابعة المزامنة.")
                commit = await GithubIntegration._request(
                    "GET", f"/repos/{link.repo_owner}/{link.repo_name}/commits/{quote_branch(link.branch)}", token)
                sha = commit["sha"]; msg = (commit.get("commit", {}).get("message") or "").split("\n")[0]
                if sha == link.last_sha and not force:
                    return f"الموقع محدَّث بالفعل (آخر commit {sha[:7]})."
                data = await GithubIntegration._request(
                    "GET", f"/repos/{link.repo_owner}/{link.repo_name}/tarball/{sha}", token,
                    want_bytes=True, limit=_GH_MAX_TARBALL)
                import json as _json
                old = _json.loads(link.manifest) if (link.manifest and link.clean) else []
                count, skipped, manifest = await Storage.install_tarball(uid, sid, data, link.subdir, old)
                await Host.redetect_kind(sid)
            except Exception as e:
                err = e.user_msg if isinstance(e, AppError) else "خطأ غير متوقع أثناء المزامنة."
                async with db() as s:
                    r = await s.execute(select(GithubLink).where(GithubLink.site_id == sid))
                    l = r.scalar_one_or_none()
                    if l: l.last_sync_error = err[:500]; s.add(l)
                await GithubIntegration._record(sid, ok=False, trigger=trigger, sha=sha, message=msg, detail=err)
                raise
            async with db() as s:
                r = await s.execute(select(GithubLink).where(GithubLink.site_id == sid))
                l = r.scalar_one_or_none()
                if l:
                    l.last_sha = sha; l.last_commit_msg = (msg or "")[:300]
                    l.last_synced_at = datetime.now(timezone.utc); l.last_sync_error = None
                    l.manifest = _json.dumps(manifest, ensure_ascii=False); s.add(l)
            await GithubIntegration._record(sid, ok=True, trigger=trigger, sha=sha, message=msg, files=count,
                                            detail=f"متجاهَل: {skipped}" if skipped else None)
            extra = f" (تم تجاهل {skipped} ملفاً غير مسموح)" if skipped else ""
            return f"تمت مزامنة {count} ملف من {link.repo_full}@{link.branch} — commit {sha[:7]}{extra}."

    @staticmethod
    def verify_webhook_signature(secret: str, payload: bytes, signature_header: str | None) -> bool:
        """تحقق من توقيع GitHub Webhook (HMAC-SHA256) لمنع أي طلب مزوَّر."""
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        import hmac
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))

    @staticmethod
    async def find_by_secret_hint(sid_hint: str) -> GithubLink | None:
        """يبحث عن ربط GitHub حسب site_id المُمرَّر بمسار الـ webhook URL."""
        try:
            sid = uuid.UUID(sid_hint)
        except ValueError:
            return None
        return await GithubIntegration.get(sid)


def quote_branch(b: str) -> str:
    from urllib.parse import quote
    return quote(b, safe="/")


# ═══════════════════════════════════════════════════════════════════
#  DOMAIN ENGINE
# ═══════════════════════════════════════════════════════════════════
class Domains:
    @staticmethod
    async def list_site(sid: uuid.UUID) -> list[Domain]:
        async with db() as s:
            r = await s.execute(select(Domain).where(Domain.site_id == sid))
            return list(r.scalars().all())

    @staticmethod
    async def get(did: uuid.UUID) -> Domain:
        async with db() as s:
            r = await s.execute(select(Domain).where(Domain.id == did))
            d = r.scalar_one_or_none()
            if not d: raise NotFoundErr("الدومين")
            return d

    @staticmethod
    async def add_custom(uid: uuid.UUID, sid: uuid.UUID, hostname: str) -> Domain:
        if not await is_custom_domains_enabled():
            raise DomainErr(user_msg="🚫 ميزة ربط الدومينات المخصصة معطَّلة حالياً من إدارة المنصة.")
        await Subs.assert_domains(uid)
        hostname = hostname.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
        cfg = get_cfg()
        protected = set(cfg.platform_hosts)
        if cfg.public_domain:
            if hostname == cfg.public_domain or hostname.endswith("." + cfg.public_domain):
                raise DomainErr(user_msg="هذا الدومين محجوز للمنصة.")
        if hostname in protected or hostname.endswith(".hf.space"):
            raise DomainErr(user_msg="هذا الدومين محجوز للمنصة.")
        async with db() as s:
            r = await s.execute(select(Domain).where(Domain.hostname == hostname))
            if r.scalar_one_or_none(): raise DomainErr(user_msg="هذا الدومين مرتبط بالفعل.")
            token = f"sawhost-{secrets.token_hex(12)}"
            d = Domain(site_id=sid, hostname=hostname, dtype=DomainType.CUSTOM, verify_token=token)
            s.add(d); await s.flush(); return d

    @staticmethod
    async def verify(did: uuid.UUID) -> Domain:
        d = await Domains.get(did)
        if not d.verify_token: raise DomainErr(user_msg="لا يوجد رمز تحقق.")
        def _lookup() -> bool:
            import dns.resolver
            answers = dns.resolver.resolve(f"_sawhost-verify.{d.hostname}", "TXT", lifetime=5.0)
            return any(d.verify_token in str(r) for r in answers)
        try:
            # الاستعلام متزامن ويحجب حتى 5 ثوانٍ: ننفّذه في خيط حتى لا يتجمّد السيرفر كله
            found = await asyncio.to_thread(_lookup)
        except Exception: found = False
        async with db() as s:
            r = await s.execute(select(Domain).where(Domain.id == did))
            domain = r.scalar_one_or_none()
            if domain:
                domain.verify_status = DomainVerify.VERIFIED if found else DomainVerify.FAILED
                s.add(domain)
        if not found: raise DomainErr(user_msg="لم يُعثَر على سجل TXT. تأكد من الإضافة وانتظر انتشار DNS (1-10 دقائق).")
        return await Domains.get(did)

    @staticmethod
    async def remove(did: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(Domain).where(Domain.id == did))
            d = r.scalar_one_or_none()
            if d:
                if d.dtype == DomainType.SUBDOMAIN: raise DomainErr(user_msg="لا يمكن حذف الـ Subdomain الأساسي.")
                if d.dtype == DomainType.PLATFORM and d.platform_domain_id:
                    pd_r = await s.execute(select(PlatformDomain).where(PlatformDomain.id == d.platform_domain_id))
                    pd = pd_r.scalar_one_or_none()
                    if pd: pd.used_by_site_id = None; s.add(pd)
                await s.delete(d)

    @staticmethod
    async def set_primary(sid: uuid.UUID, did: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(Domain).where(Domain.site_id == sid))
            for dom in r.scalars().all(): dom.is_primary = (dom.id == did); s.add(dom)

    @staticmethod
    async def resolve_host(hostname: str) -> Site | None:
        async with db() as s:
            r = await s.execute(
                select(Site).join(Domain, Domain.site_id==Site.id)
                .where(Domain.hostname==hostname.lower(), Domain.verify_status==DomainVerify.VERIFIED, Site.is_deleted.is_(False))
            )
            return r.scalar_one_or_none()


# ═══════════════════════════════════════════════════════════════════
#  PLATFORM DOMAINS ENGINE (يضيفها الأدمن، يختارها أي مستخدم لموقعه)
# ═══════════════════════════════════════════════════════════════════
class PlatformDomains:
    @staticmethod
    async def add(hostname: str, label: str = "") -> PlatformDomain:
        hostname = hostname.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
        async with db() as s:
            r = await s.execute(select(PlatformDomain).where(PlatformDomain.hostname == hostname))
            if r.scalar_one_or_none(): raise DomainErr(user_msg="هذا الدومين مضاف مسبقاً بقائمة دومينات المنصة.")
            pd = PlatformDomain(hostname=hostname, label=label)
            s.add(pd); await s.flush(); return pd

    @staticmethod
    async def list_all() -> list[PlatformDomain]:
        async with db() as s:
            r = await s.execute(select(PlatformDomain).order_by(PlatformDomain.created_at.desc()))
            return list(r.scalars().all())

    @staticmethod
    async def list_available() -> list[PlatformDomain]:
        """الدومينات المتاحة حالياً للاختيار (نشطة وغير مستخدمة)."""
        async with db() as s:
            r = await s.execute(
                select(PlatformDomain).where(PlatformDomain.is_active.is_(True), PlatformDomain.used_by_site_id.is_(None))
                .order_by(PlatformDomain.created_at.asc())
            )
            return list(r.scalars().all())

    @staticmethod
    async def get(pdid: uuid.UUID) -> PlatformDomain:
        async with db() as s:
            r = await s.execute(select(PlatformDomain).where(PlatformDomain.id == pdid))
            pd = r.scalar_one_or_none()
            if not pd: raise NotFoundErr("دومين المنصة")
            return pd

    @staticmethod
    async def assign_to_site(uid: uuid.UUID, sid: uuid.UUID, pdid: uuid.UUID) -> Domain:
        """يربط دومين منصة (اختاره المستخدم) بأحد مواقعه، ضمن حدود خطته."""
        await Subs.assert_platform_domains(uid)
        async with db() as s:
            r = await s.execute(select(PlatformDomain).where(PlatformDomain.id == pdid))
            pd = r.scalar_one_or_none()
            if not pd: raise NotFoundErr("دومين المنصة")
            if not pd.is_active: raise DomainErr(user_msg="هذا الدومين غير متاح حالياً.")
            if pd.used_by_site_id: raise DomainErr(user_msg="هذا الدومين مُستخدَم من موقع آخر بالفعل.")
            existing = await s.execute(select(Domain).where(Domain.hostname == pd.hostname))
            if existing.scalar_one_or_none(): raise DomainErr(user_msg="هذا الدومين مرتبط بالفعل.")
            pd.used_by_site_id = sid
            s.add(pd)
            d = Domain(site_id=sid, hostname=pd.hostname, dtype=DomainType.PLATFORM,
                       verify_status=DomainVerify.VERIFIED, platform_domain_id=pd.id)
            s.add(d); await s.flush(); return d

    @staticmethod
    async def remove(pdid: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(PlatformDomain).where(PlatformDomain.id == pdid))
            pd = r.scalar_one_or_none()
            if not pd: raise NotFoundErr("دومين المنصة")
            if pd.used_by_site_id: raise AppError(user_msg="لا يمكن حذف دومين مُستخدَم حالياً من قِبل مستخدم. أزِل الربط أولاً.")
            await s.delete(pd)

    @staticmethod
    async def toggle_active(pdid: uuid.UUID) -> PlatformDomain:
        async with db() as s:
            r = await s.execute(select(PlatformDomain).where(PlatformDomain.id == pdid))
            pd = r.scalar_one_or_none()
            if not pd: raise NotFoundErr("دومين المنصة")
            pd.is_active = not pd.is_active; s.add(pd); return pd


# ═══════════════════════════════════════════════════════════════════
#  BACKUP ENGINE
# ═══════════════════════════════════════════════════════════════════
class Backups:
    @staticmethod
    def _dir(sid: uuid.UUID) -> Path:
        p = Path(get_cfg().backups_root) / str(sid)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    async def create(uid: uuid.UUID, sid: uuid.UUID, trigger: BackupTrigger = BackupTrigger.MANUAL) -> Backup:
        await Subs.assert_backups(uid, sid)
        async with db() as s:
            bk = Backup(site_id=sid, trigger=trigger, status=BackupStatus.PENDING); s.add(bk); await s.flush(); bid = bk.id
        try:
            data = Storage.compress(sid, ".")
            path = Backups._dir(sid) / f"{bid}.zip"
            path.write_bytes(data)
            mb = max(1, len(data)//(1024*1024))
            async with db() as s:
                r = await s.execute(select(Backup).where(Backup.id == bid))
                bk = r.scalar_one(); bk.status = BackupStatus.COMPLETED; bk.path = str(path); bk.size_mb = mb; s.add(bk)
        except Exception as e:
            async with db() as s:
                r = await s.execute(select(Backup).where(Backup.id == bid))
                bk = r.scalar_one(); bk.status = BackupStatus.FAILED; s.add(bk)
            raise AppError(user_msg=f"فشل إنشاء النسخة: {e}")
        return bk

    @staticmethod
    async def list_site(sid: uuid.UUID) -> list[Backup]:
        async with db() as s:
            r = await s.execute(select(Backup).where(Backup.site_id==sid).order_by(Backup.created_at.desc()))
            return list(r.scalars().all())

    @staticmethod
    async def get(bid: uuid.UUID) -> Backup:
        async with db() as s:
            r = await s.execute(select(Backup).where(Backup.id==bid))
            bk = r.scalar_one_or_none()
            if not bk: raise NotFoundErr("النسخة الاحتياطية")
            return bk

    @staticmethod
    async def restore(uid: uuid.UUID, bid: uuid.UUID) -> None:
        bk = await Backups.get(bid)
        if bk.status != BackupStatus.COMPLETED or not bk.path: raise AppError(user_msg="النسخة غير صالحة.")
        p = Path(bk.path)
        if not p.exists(): raise AppError(user_msg="ملف النسخة مفقود.")
        for item in Storage.site_root(bk.site_id).iterdir(): Storage.delete(bk.site_id, item.name)
        await Storage.extract_zip(uid, bk.site_id, p.read_bytes(), ".")

    @staticmethod
    async def delete(bid: uuid.UUID) -> None:
        async with db() as s:
            r = await s.execute(select(Backup).where(Backup.id==bid))
            bk = r.scalar_one_or_none()
            if not bk: return
            path = bk.path; await s.delete(bk)
        if path:
            p = Path(path)
            if p.exists(): p.unlink()


# ═══════════════════════════════════════════════════════════════════
#  ADMIN ENGINE
# ═══════════════════════════════════════════════════════════════════
from dataclasses import dataclass

@dataclass
class Stats:
    total_users: int; banned_users: int; total_sites: int; running_sites: int
    total_domains: int; verified_domains: int; total_mb: int
    pending_requests: int; unread_notifs: int
    platform_domains_total: int; platform_domains_free: int; active_plans: int

class Admin:
    @staticmethod
    async def stats() -> Stats:
        async with db() as s:
            tu = (await s.execute(select(func.count(User.id)))).scalar_one()
            bu = (await s.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))).scalar_one()
            ts = (await s.execute(select(func.count(Site.id)).where(Site.is_deleted.is_(False)))).scalar_one()
            rs = (await s.execute(select(func.count(Site.id)).where(Site.is_deleted.is_(False),Site.status=="running"))).scalar_one()
            td = (await s.execute(select(func.count(Domain.id)))).scalar_one()
            vd = (await s.execute(select(func.count(Domain.id)).where(Domain.verify_status=="verified"))).scalar_one()
            tm = (await s.execute(select(func.coalesce(func.sum(Site.used_mb),0)).where(Site.is_deleted.is_(False)))).scalar_one()
            pr = (await s.execute(select(func.count(SubRequest.id)).where(SubRequest.status==ReqStatus.PENDING))).scalar_one()
            un = (await s.execute(select(func.count(AdminNotif.id)).where(AdminNotif.is_read.is_(False)))).scalar_one()
            pdt = (await s.execute(select(func.count(PlatformDomain.id)))).scalar_one()
            pdf = (await s.execute(select(func.count(PlatformDomain.id)).where(PlatformDomain.is_active.is_(True), PlatformDomain.used_by_site_id.is_(None)))).scalar_one()
            ap = (await s.execute(select(func.count(SubscriptionPlan.id)).where(SubscriptionPlan.is_active.is_(True)))).scalar_one()
        return Stats(tu,bu,ts,rs,td,vd,int(tm),pr,un,pdt,pdf,ap)

    @staticmethod
    async def list_users(page: int = 0, size: int = 15) -> list[User]:
        async with db() as s:
            r = await s.execute(select(User).order_by(User.created_at.desc()).offset(page*size).limit(size))
            return list(r.scalars().all())

    @staticmethod
    async def get_user(uid: uuid.UUID) -> User | None:
        async with db() as s:
            r = await s.execute(select(User).where(User.id==uid)); return r.scalar_one_or_none()

    @staticmethod
    async def get_user_by_tgid(tgid: int) -> User | None:
        async with db() as s:
            r = await s.execute(select(User).where(User.telegram_id==tgid)); return r.scalar_one_or_none()

    @staticmethod
    async def broadcast(bot, text: str) -> dict:
        import asyncio
        async with db() as s:
            r = await s.execute(select(User.telegram_id).where(User.is_banned.is_(False)))
            ids = [row[0] for row in r.all()]
        sent = failed = 0
        for tid in ids:
            try: await bot.send_message(tid, text); sent += 1
            except Exception: failed += 1
            await asyncio.sleep(0.05)
        return {"sent":sent,"failed":failed,"total":len(ids)}


# ═══════════════════════════════════════════════════════════════════
#  SUBSCRIPTION REQUEST ENGINE
# ═══════════════════════════════════════════════════════════════════
class Requests:
    @staticmethod
    async def create(uid: uuid.UUID, plan_id: uuid.UUID, months: int = 1, note: str | None = None) -> SubRequest:
        async with db() as s:
            r = await s.execute(select(SubRequest).where(SubRequest.user_id==uid, SubRequest.status==ReqStatus.PENDING))
            if r.scalar_one_or_none(): raise AppError(user_msg="لديك طلب معلّق بالفعل.")
            req = SubRequest(user_id=uid, plan_id=plan_id, months=max(1, months), note=note)
            s.add(req); await s.flush(); return req

    @staticmethod
    async def approve(rid: uuid.UUID, admin_uid: uuid.UUID) -> SubRequest:
        async with db() as s:
            r = await s.execute(
                select(SubRequest).options(selectinload(SubRequest.plan)).where(SubRequest.id==rid)
            )
            req = r.scalar_one_or_none()
            if not req: raise NotFoundErr("الطلب")
            if req.status != ReqStatus.PENDING: raise AppError(user_msg="الطلب تمت معالجته مسبقًا.")
            req.status = ReqStatus.APPROVED; req.admin_note = "✅ تمت الموافقة"; s.add(req)
            sub_r = await s.execute(select(Subscription).where(Subscription.user_id==req.user_id)); sub = sub_r.scalar_one_or_none()
            if sub:
                now = datetime.now(timezone.utc)
                cur_exp = as_aware(sub.expires_at)
                base = cur_exp if (cur_exp and cur_exp > now) else now
                sub.expires_at = base + timedelta(days=30 * req.months)
                sub.plan_id = req.plan_id
                s.add(sub)
            return req

    @staticmethod
    async def reject(rid: uuid.UUID, reason: str) -> SubRequest:
        async with db() as s:
            r = await s.execute(select(SubRequest).where(SubRequest.id==rid)); req = r.scalar_one_or_none()
            if not req: raise NotFoundErr("الطلب")
            if req.status != ReqStatus.PENDING: raise AppError(user_msg="الطلب تمت معالجته مسبقًا.")
            req.status = ReqStatus.REJECTED; req.admin_note = reason; s.add(req); return req

    @staticmethod
    async def list_pending(page: int = 0) -> list[SubRequest]:
        async with db() as s:
            r = await s.execute(
                select(SubRequest).options(selectinload(SubRequest.plan), selectinload(SubRequest.user))
                .where(SubRequest.status==ReqStatus.PENDING).order_by(SubRequest.created_at.asc()).offset(page*10).limit(10)
            )
            return list(r.scalars().all())

    @staticmethod
    async def get(rid: uuid.UUID) -> SubRequest | None:
        async with db() as s:
            r = await s.execute(
                select(SubRequest).options(selectinload(SubRequest.plan), selectinload(SubRequest.user)).where(SubRequest.id==rid)
            )
            return r.scalar_one_or_none()
