from __future__ import annotations

"""Delivery service: records delivery attempts and sends email when SMTP is set.

A digest is always saved to history first; delivery is a best-effort follow-up.
When SMTP is not configured (host/username/password empty), the attempt is
recorded as SKIPPED without blocking generation.
"""

import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Delivery, DeliveryStatus, Digest, DigestItem, Subscription, User

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_username and settings.smtp_password)


async def record_delivery(
    session: AsyncSession,
    *,
    digest: Digest,
    channel: str,
    status: DeliveryStatus,
    recipient: str = "",
    error: str = "",
) -> Delivery:
    delivery = Delivery(
        digest_id=digest.id,
        channel=channel,
        status=status,
        recipient=recipient,
        error=error,
    )
    session.add(delivery)
    await session.flush()
    await session.commit()
    return delivery


async def deliver_digest(
    session: AsyncSession,
    digest: Digest,
    sub: Subscription,
    user: User,
    *,
    channel: str = "email",
) -> Delivery:
    """Deliver a saved digest. Records the attempt; never raises on send failure."""
    if not smtp_configured():
        logger.info("SMTP not configured; digest %s saved to history only", digest.id)
        return await record_delivery(
            session,
            digest=digest,
            channel=channel,
            status=DeliveryStatus.SKIPPED,
            recipient=user.email or "",
            error="SMTP 未配置，跳过邮件发送",
        )

    try:
        items = (
            await session.execute(
                select(DigestItem)
                .where(DigestItem.digest_id == digest.id)
                .order_by(DigestItem.rank)
            )
        ).scalars().all()
        msg = EmailMessage()
        msg["Subject"] = digest.title or "AI 新闻简报"
        msg["From"] = settings.smtp_from
        msg["To"] = user.email
        msg.set_content(_render_body(digest, items), subtype="plain", charset="utf-8")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        return await record_delivery(
            session,
            digest=digest,
            channel=channel,
            status=DeliveryStatus.SENT,
            recipient=user.email,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("email delivery failed for digest %s: %s", digest.id, exc)
        return await record_delivery(
            session,
            digest=digest,
            channel=channel,
            status=DeliveryStatus.FAILED,
            recipient=user.email or "",
            error=str(exc)[:2000],
        )


def _render_body(digest: Digest, items: list[DigestItem]) -> str:
    """Plain-text email body from a saved digest (items passed explicitly to
    avoid lazy-loading in an async context)."""
    lines = [f"# {digest.title or 'AI 新闻简报'}", "", digest.overview or "", ""]
    for item in items:
        lines.append(f"- {item.title}")
        if item.summary:
            lines.append(f"  {item.summary}")
        lines.append(f"  {item.url}")
        lines.append("")
    return "\n".join(lines)
