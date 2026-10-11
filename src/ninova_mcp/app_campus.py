"""Campus life tools that need no login: directory, shuttles, buildings, sports, announcements."""

from __future__ import annotations

from typing import Any

from .public_client import ItuPublicClient


class CampusToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``itu_public``."""

    itu_public: ItuPublicClient

    def search_itu_directory(
        self,
        first_name: str,
        last_name: str,
        identity_type: str = "all",
        include_details: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the official public İTÜ directory."""
        return self.itu_public.search_directory(
            first_name,
            last_name,
            identity_type=identity_type,
            include_details=include_details,
            limit=limit,
        )

    def get_shuttle_schedule(
        self,
        route: str | None = None,
        day_type: str | None = None,
    ) -> dict[str, Any]:
        """Read official SKS shuttle/ring schedules and stop lists."""
        return self.itu_public.get_shuttle_schedule(route=route, day_type=day_type)

    def search_campus_locations(self, query: str | None = None) -> dict[str, Any]:
        """Search official OBS building codes and names."""
        return self.itu_public.search_campus_locations(query)

    def get_sports_facility_hours(self, facility: str | None = None) -> dict[str, Any]:
        """Read official opening hours for İTÜ sports facilities."""
        return self.itu_public.get_sports_facility_hours(facility)

    def get_itu_announcements(
        self,
        sources: list[str] | None = None,
        query: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Aggregate announcements from official İTÜ, ÖDEK, İKM, SKS and Erasmus sources."""
        return self.itu_public.get_announcements(sources=sources, query=query, limit=limit)
