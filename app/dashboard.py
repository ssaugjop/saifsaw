"""
SAWHost - لوحة الإدارة القديمة (/dashboard)
===========================================
أُلغيت لصالح لوحة الويب الجديدة. كانت تعتمد Basic Auth بكلمة مرور افتراضية
معروفة، لذلك صارت كل مساراتها تحوّل إلى قسم الإدارة في /panel (الذي يتطلب
تسجيل دخول حقيقياً وصلاحية أدمن).
"""
from __future__ import annotations

from aiohttp import web


async def _to_admin(req: web.Request) -> web.Response:
    raise web.HTTPFound("/panel/#/admin")


def register_dashboard(app: web.Application) -> None:
    for path in ("/dashboard", "/dashboard/", "/dashboard/users", "/dashboard/sites",
                 "/dashboard/req", "/dashboard/notifs"):
        app.router.add_get(path, _to_admin)
