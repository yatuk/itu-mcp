"""İTÜ Library tools: catalog search, availability, account, loans."""

from __future__ import annotations

from typing import Any

from .library_client import LibraryClient


class LibraryToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``library``."""

    library: LibraryClient

    def library_search(
        self,
        query: str,
        search_type: str = "keyword",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the public İTÜ Library catalog."""
        return self.library.search(query, search_type=search_type, limit=limit, offset=offset)

    def library_get_item(self, record_id: str) -> dict[str, Any]:
        """Read a public library catalog record."""
        return self.library.get_item(record_id)

    def library_check_availability(self, record_id: str) -> dict[str, Any]:
        """Read copy-level availability for a public library record."""
        return self.library.check_availability(record_id)

    def library_get_account(self) -> dict[str, Any]:
        """Read the separate İTÜ Library patron account."""
        return self.library.get_account()

    def library_list_loans(self) -> dict[str, Any]:
        """List current library loans and renewal identifiers."""
        return self.library.list_loans()

    def library_renew_loan(self, loan_id: str, confirm: bool = False) -> dict[str, Any]:
        """Preview or explicitly confirm renewal of one library loan."""
        return self.library.renew_loan(loan_id, confirm=confirm)

    def library_reserve_item(
        self,
        record_id: str,
        pickup_location: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Preview or explicitly confirm a library hold request."""
        return self.library.reserve_item(
            record_id,
            pickup_location=pickup_location,
            confirm=confirm,
        )
