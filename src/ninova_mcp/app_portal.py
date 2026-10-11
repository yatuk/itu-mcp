"""İTÜ Portal tools: campus card, cafeteria menu, notifications, help tickets and storage quota.

All of them need a login: each one goes through the Portal SSO session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .client import NinovaClient, NinovaError
from .parsing import normalize_lookup_text


class PortalToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``client`` and the course cache."""

    client: NinovaClient

    def obs_get_campus_card(self) -> dict[str, Any]:
        """Read campus card balance and recent transactions from the İTÜ Portal."""
        from .portal_data import campus_card

        return campus_card(self._get_portal_json("GetBalance"))

    def _get_portal_page(self) -> tuple[str, str]:
        """Fetch the portal homepage once, cached for the request lifetime."""
        cache_key = "portal_home"
        cached = self._course_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        html, url = self.client.get_portal_html("/apps/default/")
        self._course_cache.set(cache_key, (html, url))
        return html, url

    def _get_portal_json(
        self,
        operation: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call a fixed read-only Portal WebMethod after establishing SSO."""
        allowed = {
            "GetFoodMenu2",
            "GetNotification",
            "GetYardim",
            "GetBalance",
            "GetQuota",
            "GetQuotaEski",
        }
        if operation not in allowed:
            raise NinovaError(f"Portal operation not allowed: {operation}")
        self._get_portal_page()
        url = f"{self.client.PORTAL_BASE_URL}/apps/default/service/service.aspx/{operation}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.client.PORTAL_BASE_URL}/apps/default/",
        }
        self.client._throttle()
        response = self.client._safe_request(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout=30,
        )
        if self.client._looks_like_login_page(response):
            self.client.login(force=True)
            self._course_cache.invalidate("portal_home")
            self._get_portal_page()
            self.client._throttle()
            response = self.client._safe_request(
                "GET",
                url,
                params=params,
                headers=headers,
                timeout=30,
            )
        if response.status_code >= 400:
            raise NinovaError(
                f"Portal {operation} isteği HTTP {response.status_code} ile başarısız oldu."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise NinovaError(f"Portal {operation} JSON yanıtı ayrıştırılamadı.") from exc
        data = payload.get("d") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise NinovaError(f"Portal {operation} beklenen 'd' nesnesini döndürmedi.")
        return data

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

    def obs_get_notifications(
        self,
        notification_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Read İTÜ Portal notifications (requires login)."""
        from .parsing import clean_text, make_soup

        try:
            data = self._get_portal_json("GetNotification")
        except (NinovaError, ValueError):
            html, url = self._get_portal_page()
            from .parsing import extract_notifications

            fallback = extract_notifications(html, url)
            notifications = fallback.get("notifications") or []
            if notification_id:
                target = next(
                    (item for item in notifications if str(item.get("id") or item.get("notification_id") or "") == str(notification_id)),
                    None,
                )
                if target is None:
                    raise NinovaError(f"Portal bildirimi bulunamadı: {notification_id}")
                return {"notification": target, "source": url, "detail_available": False, "untrusted_external_content": True}
            fallback["notifications"] = notifications[: max(1, min(limit, 100))]
            fallback["count"] = len(fallback["notifications"])
            fallback["api_fallback"] = True
            fallback["untrusted_external_content"] = True
            return fallback
        notifications = []
        for item in data.get("NotificationInformationList") or []:
            if not isinstance(item, dict):
                continue
            content_html = str(item.get("ContentText") or "")
            notifications.append({
                "id": str(item.get("ObjectId") or ""),
                "title": clean_text(str(item.get("Title") or "")),
                "content": clean_text(make_soup(content_html).get_text(" ", strip=True)),
                "created_at": item.get("CreateDate"),
                "age": item.get("BeforeCreateDate"),
                "unread": item.get("IsRead") in {0, "0", False},
            })
        if notification_id:
            target = next((item for item in notifications if item["id"] == str(notification_id)), None)
            if target is None:
                raise NinovaError(f"Portal bildirimi bulunamadı: {notification_id}")
            return {"notification": target, "source": "portal.itu.edu.tr/GetNotification", "untrusted_external_content": True}
        notifications = notifications[: max(1, min(limit, 100))]
        return {"count": len(notifications), "notifications": notifications, "source": "portal.itu.edu.tr/GetNotification", "untrusted_external_content": True}

    def _read_help_ticket(self, identifier: str) -> dict[str, Any]:
        """Fetch and parse one ticket page. Callers decide ownership and redaction."""
        from .help_tickets import help_url, parse_detail, ticket_url

        cache_key = f"help_ticket:{identifier}"
        cached = self._course_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        from requests import RequestException

        try:
            html, response = self.client.get_html(ticket_url(identifier))
        except RequestException as exc:
            raise NinovaError("Help ticket HTTP read failed.") from exc
        if help_url(response.url) is None:
            raise NinovaError("Help ticket read did not reach the authenticated help desk.")
        ticket = parse_detail(html, response.url, identifier)
        if ticket.get("id") != identifier:
            raise NinovaError(ticket.get("parse_warning") or "Help ticket was not found or is inaccessible.")
        self._course_cache.set(cache_key, ticket)
        return ticket

    def _help_ticket_rows(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """The signed-in user's own ticket list, from the Portal widget."""
        from .help_tickets import summary
        from .parsing import extract_help_tickets

        try:
            data = self._get_portal_json("GetYardim")
        except (NinovaError, ValueError):
            data = {}
        raw_items = data.get("YardimInformationList") or data.get("HelpInformationList") or []
        tickets = [summary(item) for item in raw_items if isinstance(item, dict)]
        if tickets:
            return tickets, {"source": "portal.itu.edu.tr/GetYardim"}
        html, url = self._get_portal_page()
        payload = extract_help_tickets(html, url)
        tickets = payload["tickets"]
        payload["api_fallback"] = True
        return tickets, payload

    def get_help_ticket(self, ticket_id: str, include_sensitive: bool = False) -> dict[str, Any]:
        """Read one of the user's own help tickets without mutations."""
        from .help_tickets import redact_ticket, ticket_id as valid_id

        identifier = valid_id(ticket_id)
        if identifier is None:
            raise NinovaError("Help ticket ID must contain only decimal digits (1–20 characters).")
        # Ticket IDs are sequential. Only IDs from the user's own Portal list
        # are read, so this tool cannot be pointed at someone else's ticket.
        own_ids = {str(item["id"]) for item in self._help_ticket_rows()[0] if item.get("id")}
        if identifier not in own_ids:
            raise NinovaError(
                "Help ticket ID is not in your own ticket list. "
                "Call obs_get_help_tickets to see the IDs you can read."
            )
        ticket = redact_ticket(self._read_help_ticket(identifier), include_sensitive=bool(include_sensitive))
        return {"ticket": ticket, "untrusted_external_content": True}

    def obs_get_help_tickets(
        self, query: str | None = None, limit: int = 20, include_details: bool = False,
    ) -> dict[str, Any]:
        """List help tickets. Detail pages are opened only when include_details is true."""
        from .help_tickets import SUMMARY_FIELDS

        tickets, payload = self._help_ticket_rows()
        if query:
            target = normalize_lookup_text(query)
            tickets = [item for item in tickets if target in normalize_lookup_text(
                " ".join(str(item.get(key) or "") for key in ("id", "title", "status")))]
        payload["total_matching_count"] = len(tickets)
        selected = tickets[:max(1, min(limit, 100))]
        for item in selected:
            if include_details and item["id"]:
                try:
                    detail = self._read_help_ticket(item["id"])
                except (NinovaError, ValueError):
                    item["detail_available"] = False
                    item["detail_warning"] = "Help ticket details were unavailable. Portal fields are retained."
                else:
                    for key in SUMMARY_FIELDS:
                        if detail.get(key) is not None:
                            item[key] = detail[key]
                    item["detail_available"] = True
                    if detail.get("parse_warning"):
                        item["detail_warning"] = detail["parse_warning"]
            item["missing_fields"] = [key for key in SUMMARY_FIELDS if item.get(key) is None]
        payload["tickets"] = selected
        payload["count"] = len(selected)
        payload["metadata_complete"] = all(not item["missing_fields"] for item in selected)
        payload["untrusted_external_content"] = True
        return payload

    def obs_get_cloud_quota(self) -> dict[str, Any]:
        """Read İTÜ Mail and İTÜ Bulut storage quota from the Portal (requires login)."""
        from .portal_data import storage_quota

        return storage_quota(self._get_portal_json)
