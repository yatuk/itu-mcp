"""İTÜ Library tools: catalog search, availability, account, loans."""

from __future__ import annotations

from typing import Any

from .article_search import ArticleSearchClient
from .library_client import LibraryClient
from .proquest_client import ProQuestClient
from .library_databases import LibraryDatabasesClient


class LibraryToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``library``."""

    library: LibraryClient
    articles: ArticleSearchClient
    proquest: ProQuestClient
    library_databases: LibraryDatabasesClient

    def library_search(
        self,
        query: str,
        search_type: str = "keyword",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the public İTÜ Library catalog."""
        return self.library.search(query, search_type=search_type, limit=limit, offset=offset)

    def search_articles(
        self,
        query: str,
        from_year: int | None = None,
        to_year: int | None = None,
        open_access_only: bool = False,
        article_type: str | None = None,
        sort: str = "relevance",
        limit: int = 10,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search journal articles in the open OpenAlex index, with İTÜ access links."""
        return self.articles.search(
            query,
            from_year=from_year,
            to_year=to_year,
            open_access_only=open_access_only,
            article_type=article_type,
            sort=sort,
            limit=limit,
            page=page,
        )

    def proquest_search(
        self,
        query: str,
        full_text_only: bool = True,
        peer_reviewed_only: bool = False,
        from_year: int | None = None,
        to_year: int | None = None,
    ) -> dict[str, Any]:
        """Search İTÜ's ProQuest subscription with the library account (opt-in)."""
        return self.proquest.search(
            query,
            full_text_only=full_text_only,
            peer_reviewed_only=peer_reviewed_only,
            from_year=from_year,
            to_year=to_year,
        )

    def library_search_databases(
        self,
        query: str | None = None,
        subject: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search the İTÜ Library's licensed article and e-book databases."""
        return self.library_databases.search(query=query, subject=subject, limit=limit)

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
