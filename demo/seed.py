"""Seed the synthetic demo organization, role accounts, and supplier catalog.

Usage:  python -m demo.seed [--reset-passwords]

Passwords are taken from FB_DEMO_PASSWORD if set (required in production), otherwise generated per
account and written to var/demo_credentials.txt (git-ignored). Passwords are never printed. Refuses
to run when DEMO_SEED_ENABLED=false. Re-running is idempotent and does not rotate passwords unless asked.
Projects are NOT seeded: the demo creates them through the API (see scripts/demo_flow.py).
"""

from __future__ import annotations

import argparse
import csv
import os
import secrets
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_factory
from app.core.security import hash_password
from app.models import Organization, SupplierOption, User

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORG_SLUG = "demo-hardware-labs"
DEMO_USERS = [
    ("customer@demo.example", "Casey Customer", "customer"),
    ("engineer@demo.example", "Erin Engineer", "engineer"),
    ("engineer2@demo.example", "Eli Engineer", "engineer"),
    ("approver@demo.example", "Avery Approver", "approver"),
    ("approver2@demo.example", "Alex Approver", "approver"),
    ("admin@demo.example", "Ada Admin", "admin"),
]


def seed_org(db: Session, *, password: str | None = None, reset_passwords: bool = False) -> dict[str, str]:
    org = db.execute(select(Organization).where(Organization.slug == ORG_SLUG)).scalar_one_or_none()
    if org is None:
        org = Organization(name="Demo Hardware Labs", slug=ORG_SLUG)
        db.add(org)
        db.flush()
    created: dict[str, str] = {}
    for email, name, role in DEMO_USERS:
        user = db.execute(select(User).where(User.organization_id == org.id, User.email == email)).scalar_one_or_none()
        if user is None or reset_passwords:
            pw = password or secrets.token_urlsafe(12)
            if user is None:
                db.add(
                    User(
                        organization_id=org.id,
                        email=email,
                        full_name=name,
                        role=role,
                        password_hash=hash_password(pw),
                        is_active=True,
                    )
                )
            else:
                user.password_hash = hash_password(pw)
            created[email] = pw
    with open(FIXTURES / "supplier_catalog.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            exists = db.execute(
                select(SupplierOption).where(
                    SupplierOption.organization_id == org.id, SupplierOption.option_code == row["option_code"]
                )
            ).first()
            if exists:
                continue
            db.add(
                SupplierOption(
                    organization_id=org.id,
                    option_code=row["option_code"],
                    vendor_name=row["vendor_name"],
                    part_number=row["part_number"],
                    part_revision=row["part_revision"] or None,
                    available_qty=int(row["available_qty"]),
                    lead_days=int(row["lead_days"]),
                    unit_cost_usd=Decimal(row["unit_cost_usd"]),
                    approved_for_part=row["approved_for_part"] == "true",
                    synthetic=True,
                )
            )
    db.flush()
    return created


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-passwords", action="store_true")
    args = parser.parse_args()
    s = get_settings()
    if not s.demo_seed_enabled:
        print("DEMO_SEED_ENABLED=false; refusing to seed demo accounts.", file=sys.stderr)
        return 2
    password = os.environ.get("FB_DEMO_PASSWORD")
    # Production hosts keep stdout in shared logs and have ephemeral disks, so the password must come from a secret.
    if s.environment == "production" and (not password or len(password) < 16):
        print("Production seeding requires FB_DEMO_PASSWORD (>= 16 chars) from the secret store.", file=sys.stderr)
        return 2
    with session_factory()() as db:
        created = seed_org(db, password=password, reset_passwords=args.reset_passwords)
        db.commit()
    if not created:
        print("Seed complete; accounts already existed (passwords unchanged; use --reset-passwords).")
        return 0
    print("Seeded demo accounts (synthetic organization 'Demo Hardware Labs'):")
    print("\n".join(f"  {email}" for email in created))
    if password:
        print("Passwords: the value of FB_DEMO_PASSWORD (not printed).")
    else:
        out = Path(os.environ.get("FB_CREDENTIALS_FILE", "var/demo_credentials.txt"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(f"{email}  {pw}\n" for email, pw in created.items()), encoding="utf-8")
        print(f"Generated passwords written to {out} (gitignored; not printed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
