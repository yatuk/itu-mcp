"""Read-only İTÜ Mail tools: inbox and sent headers, one message, one attachment."""

from __future__ import annotations

import json
from typing import Any

from .mail_client import ItuMailClient


class MailToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``mail``."""

    mail: ItuMailClient

    def mail_status(self) -> dict[str, Any]:
        """Check the read-only İTÜ Mail connection and inbox counts."""
        return self.mail.status()

    def mail_list_inbox(
        self,
        unread_only: bool = False,
        since_days: int = 14,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List recent inbox headers without marking messages as read."""
        return self.mail.list_inbox(
            unread_only=unread_only,
            since_days=since_days,
            limit=limit,
        )

    def mail_list_sent(self, since_days: int = 14, limit: int = 20) -> dict[str, Any]:
        """List saved sent-message headers without changing message flags."""
        return self.mail.list_sent(since_days=since_days, limit=limit)

    def mail_get_message(
        self, uid: str, max_chars: int = 12_000, mailbox: str = "INBOX",
    ) -> dict[str, Any]:
        """Read one message using the UID and mailbox returned by its listing."""
        return self.mail.get_message(uid, max_chars=max_chars, mailbox=mailbox)

    def mail_get_attachment(
        self,
        uid: str,
        part_id: str,
        max_chars: int = 50_000,
        mailbox: str = "INBOX",
    ) -> Any:
        """Inspect one allowlisted PDF/JPEG/PNG attachment without executing it."""
        result = self.mail.get_attachment(uid, part_id, max_chars=max_chars, mailbox=mailbox)
        if result.get("kind") != "image":
            return result

        from mcp.server.fastmcp.utilities.types import Image
        from mcp.types import TextContent

        image_bytes = result.pop("image_bytes")
        image_format = "jpeg" if result["content_type"] == "image/jpeg" else "png"
        metadata = json.dumps(result, ensure_ascii=False, indent=2)
        return [
            TextContent(type="text", text=metadata),
            Image(data=image_bytes, format=image_format),
        ]
