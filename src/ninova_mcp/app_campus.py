"""Campus life tools: cafeteria, directory, shuttles, buildings, sports, announcements."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .client import NinovaError
from .public_client import ItuPublicClient
from .parsing import normalize_lookup_text


class CampusToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``itu_public``.

    The cafeteria menu is read through ``PortalToolsMixin._get_portal_json``.
    """

    itu_public: ItuPublicClient

    def get_cafeteria_menu(
        self,
        date: str | None = None,
        meal: str | None = None,
        vegan: bool = False,
    ) -> dict[str, Any]:
        """Read a dated lunch/dinner menu from the authenticated İTÜ Portal."""
        if date:
            parsed_date = None
            for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
                try:
                    parsed_date = datetime.strptime(date, fmt)
                    break
                except ValueError:
                    continue
            if parsed_date is None:
                raise NinovaError("date, YYYY-MM-DD veya DD.MM.YYYY biçiminde olmalı.")
        else:
            parsed_date = datetime.now()
        date_value = parsed_date.strftime("%d.%m.%Y")
        meal_key = normalize_lookup_text(meal or ("dinner" if datetime.now().hour >= 14 else "lunch"))
        meal_map = {"lunch": "ogle", "ogle": "ogle", "dinner": "aksam", "aksam": "aksam"}
        if meal_key not in meal_map:
            raise NinovaError("meal, lunch/öğle veya dinner/akşam olmalı.")
        period = meal_map[meal_key]
        ogun_key = f"itu-{period}-yemegi-{'vegan' if vegan else 'genel'}"
        data = self._get_portal_json(
            "GetFoodMenu2",
            params={
                "notIncludeKey": "'ana-yemek'" if vegan else "'vejeteryan'",
                "ogunKey": f"'{ogun_key}'",
                "date": f"'{date_value}'",
            },
        )
        foods = []
        for item in data.get("FoodList") or []:
            if not isinstance(item, dict):
                continue
            foods.append({
                "id": item.get("ObjectId"),
                "name": item.get("FoodName"),
                "type": item.get("FoodType"),
                "kcal": item.get("TotalWeight"),
                "allergens": [
                    {"name": effect.get("SideEffectName"), "name_en": effect.get("SideEffectNameEN")}
                    for effect in (item.get("FoodSideEffectInformationList") or [])
                    if isinstance(effect, dict)
                ],
            })
        return {
            "date": date_value,
            "meal": "lunch" if period == "ogle" else "dinner",
            "vegan": vegan,
            "status_code": data.get("StatusCode"),
            "count": len(foods),
            "items": foods,
            "menu_total_kcal": data.get("MenuTotalKcal"),
            "source": "portal.itu.edu.tr/GetFoodMenu2",
            "untrusted_external_content": True,
        }

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
