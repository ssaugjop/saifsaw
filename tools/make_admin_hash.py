#!/usr/bin/env python3
"""يولّد بصمة PBKDF2 لكلمة مرور الأدمن (اختياري). الاستخدام:  python tools/make_admin_hash.py"""
import base64, getpass, hashlib, os, sys

pw = getpass.getpass("كلمة المرور (12 حرفاً على الأقل): ")
if len(pw) < 12:
    sys.exit("قصيرة جداً.")
if pw != getpass.getpass("أعد كتابتها: "):
    sys.exit("غير متطابقتين.")
salt = os.urandom(16)
dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 600_000)
print("\nضعها في Secret باسم ADMIN_PASS_HASH:\n")
print(f"pbkdf2_sha256$600000${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}")
