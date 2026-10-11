"""ITU Portal widgets: campus card, notifications, help tickets and the cafeteria menu."""

from __future__ import annotations

from typing import Any

from .parsing_common import clean_text, make_soup, normalize_lookup_text


def extract_campus_card_info(
    html: str,
    page_url: str,
    base_url: str = "",
) -> dict[str, Any]:
    """Parse the İTÜ Portal campus card widget.

    Selectors (portal ``/apps/default/``):
    - Balance: ``div[data-placement="balance"]``
    - Transactions: ``ul[data-placement="transitions"] li.card-deposit__list-item``
    """
    soup = make_soup(html)

    balance: str | None = None
    transactions: list[dict[str, Any]] = []

    # Balance
    balance_el = soup.select_one('div[data-placement="balance"]')
    if balance_el:
        balance = clean_text(balance_el.get_text(" ", strip=True))

    # Transactions
    tx_container = soup.select_one('ul[data-placement="transitions"]')
    if tx_container:
        for li in tx_container.select("li.card-deposit__list-item"):
            spans = li.find_all("span", class_="amount")
            icon = li.find("span", class_=True)
            icon_class = ""
            if icon:
                classes = icon.get("class", [])
                icon_class = next((c for c in classes if c != "amount" and "icon" in c), "")

            tx_type: str | None = None
            if "spending" in icon_class:
                tx_type = "Harcama"
            elif "charge" in icon_class:
                tx_type = "Yükleme"

            amounts = [clean_text(s.get_text(" ", strip=True)) for s in spans]
            text = clean_text(li.get_text(" ", strip=True))

            # First row is header "Bakiye Tutar", skip it
            if normalize_lookup_text(text).startswith("bakiye"):
                continue

            transactions.append({
                "type": tx_type,
                "amounts": amounts,
                "description": text,
            })

    return {
        "url": page_url,
        "balance": balance,
        "transactions": transactions[:20],
        "transaction_count": len(transactions),
    }


def extract_notifications(
    html: str,
    page_url: str,
) -> dict[str, Any]:
    """Parse the İTÜ Portal notifications widget.

    Selectors (portal ``/apps/default/``):
    - List: ``ul[data-placement="notification-list"] li.notification__list-item``
    """
    soup = make_soup(html)
    items: list[dict[str, Any]] = []

    container = soup.select_one('ul[data-placement="notification-list"]')
    if container:
        for li in container.select("li.notification__list-item"):
            anchor = li.find("a", href=True)
            title_span = anchor.find("span", class_="pull-left") if anchor else None
            time_span = anchor.find("span", class_="pull-right") if anchor else None
            title = clean_text(title_span.get_text(" ", strip=True)) if title_span else None
            time_ago = clean_text(time_span.get_text(" ", strip=True)) if time_span else None
            is_unread = "notification__list-item--unread" in (li.get("class") or [])
            notif_id = anchor.get("data-notification-id") if anchor else None
            if title:
                items.append({
                    "title": title,
                    "time_ago": time_ago,
                    "unread": is_unread,
                    "notification_id": notif_id,
                })

    return {
        "url": page_url,
        "count": len(items),
        "notifications": items[:20],
    }


def extract_help_tickets(
    html: str,
    page_url: str,
) -> dict[str, Any]:
    """Parse all help widget rows before applying tool filters and limits."""
    from .help_tickets import parse_widget

    return parse_widget(html, page_url)


def extract_cafeteria_menu(
    html: str,
    page_url: str,
    base_url: str = "https://portal.itu.edu.tr",
) -> dict[str, Any]:
    """Parse the İTÜ Portal cafeteria menu widget from ``/apps/default/``.

    Selectors:
    - Title: ``span[data-placement="food-title"]``
    - Items: ``ul[data-placement="food-list"] li.lunch-menu__list-item``
    - Radio (öğle/akşam): ``#radio-ogle`` / ``#radio-aksam``
    - Date: ``#food-date``
    - Vegetarian checkbox: ``#checkbox-vejeteryan-vegan``
    """
    soup = make_soup(html)

    # Meal type (öğle/akşam)
    meal_type: str | None = None
    oglen_radio = soup.select_one("#radio-ogle")
    aksam_radio = soup.select_one("#radio-aksam")
    if oglen_radio and "checked" in (oglen_radio.get("checked") or ""):
        meal_type = "Öğle Yemeği"
    elif aksam_radio and "checked" in (aksam_radio.get("checked") or ""):
        meal_type = "Akşam Yemeği"
    # Fallback: read the title
    if not meal_type:
        title_el = soup.select_one('span[data-placement="food-title"]')
        if title_el:
            meal_type = clean_text(title_el.get_text(" ", strip=True))

    # Title
    title_el = soup.select_one('span[data-placement="food-title"]')
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else None

    # Date
    date: str | None = None
    date_input = soup.select_one("#food-date")
    if date_input:
        date = date_input.get("value") or None

    # Vegetarian option
    vegan_cb = soup.select_one("#checkbox-vejeteryan-vegan")
    vegetarian_available = vegan_cb is not None
    vegetarian_selected = bool(vegan_cb and "checked" in (vegan_cb.get("checked") or ""))

    # Menu items
    items: list[dict[str, Any]] = []
    food_list = soup.select_one('ul[data-placement="food-list"]')
    if food_list:
        for li in food_list.find_all("li", class_="lunch-menu__list-item", recursive=True):
            # Extract text without the warning icon
            warning_icon = li.find("i", class_="icon-warning")
            food_id = None
            has_allergen_info = False
            if warning_icon:
                has_allergen_info = True
                food_id = warning_icon.get("data-food-id")
                warning_icon.decompose()  # Remove icon so get_text gives clean name
            name = clean_text(li.get_text(" ", strip=True))
            if name:
                items.append({
                    "name": name,
                    "food_id": food_id,
                    "has_allergen_info": has_allergen_info,
                })

        # Check for "Seçmeli 4. Çeşit" (optional 4th dish) — a <b> tag with sub-list
        selectable_header = food_list.find("b")
        if selectable_header:
            selectable_name = clean_text(selectable_header.get_text(" ", strip=True))
            selectable_items: list[str] = []
            selectable_ul = selectable_header.find_next("ul", class_="secmeli-yemek")
            if selectable_ul:
                for sli in selectable_ul.find_all("li", class_="lunch-menu__list-item"):
                    sname = clean_text(sli.get_text(" ", strip=True))
                    if sname:
                        selectable_items.append(sname)
            if selectable_items:
                items.append({
                    "name": selectable_name,
                    "options": selectable_items,
                    "is_selectable_group": True,
                })

    return {
        "url": page_url,
        "title": title,
        "meal_type": title or meal_type,
        "date": date,
        "vegetarian_available": vegetarian_available,
        "vegetarian_selected": vegetarian_selected,
        "items": items,
        "item_count": len(items),
    }
