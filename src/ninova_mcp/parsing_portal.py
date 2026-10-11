"""ITU Portal widgets: campus card, notifications, help tickets and the cafeteria menu."""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

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


def _is_checked(control: Tag | None) -> bool:
    """True for ``checked="checked"``. A bare ``checked`` parses to an empty value and is not counted."""
    return bool(control and "checked" in (control.get("checked") or ""))


def _checked_meal_type(soup: BeautifulSoup) -> str | None:
    if _is_checked(soup.select_one("#radio-ogle")):
        return "Öğle Yemeği"
    if _is_checked(soup.select_one("#radio-aksam")):
        return "Akşam Yemeği"
    return None


def _cafeteria_dish(li: Tag) -> dict[str, Any] | None:
    """One menu line, or None when the line has no text besides its warning icon."""
    warning_icon = li.find("i", class_="icon-warning")
    has_allergen_info = warning_icon is not None
    food_id = None
    if warning_icon is not None:
        food_id = warning_icon.get("data-food-id")
        warning_icon.decompose()  # Remove icon so get_text gives clean name
    name = clean_text(li.get_text(" ", strip=True))
    if not name:
        return None
    return {
        "name": name,
        "food_id": food_id,
        "has_allergen_info": has_allergen_info,
    }


def _cafeteria_selectable_group(food_list: Tag) -> dict[str, Any] | None:
    """The "Seçmeli 4. Çeşit" group: a ``<b>`` title followed by a sub-list of options."""
    header = food_list.find("b")
    if header is None:
        return None
    options_list = header.find_next("ul", class_="secmeli-yemek")
    if options_list is None:
        return None
    options = [
        name
        for li in options_list.find_all("li", class_="lunch-menu__list-item")
        if (name := clean_text(li.get_text(" ", strip=True)))
    ]
    if not options:
        return None
    return {
        "name": clean_text(header.get_text(" ", strip=True)),
        "options": options,
        "is_selectable_group": True,
    }


def _cafeteria_items(food_list: Tag) -> list[dict[str, Any]]:
    # The options of the selectable group are list items too, so they are
    # listed once as dishes and once more inside the group.
    dishes = [_cafeteria_dish(li) for li in food_list.find_all("li", class_="lunch-menu__list-item", recursive=True)]
    items = [dish for dish in dishes if dish is not None]
    group = _cafeteria_selectable_group(food_list)
    if group is not None:
        items.append(group)
    return items


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

    title_el = soup.select_one('span[data-placement="food-title"]')
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else None
    # The title wins; the checked radio is the fallback. An empty title with
    # no checked radio stays an empty string instead of becoming None.
    meal_type = title or _checked_meal_type(soup) or title

    date_input = soup.select_one("#food-date")
    date = (date_input.get("value") or None) if date_input else None

    vegan_cb = soup.select_one("#checkbox-vejeteryan-vegan")

    food_list = soup.select_one('ul[data-placement="food-list"]')
    items = _cafeteria_items(food_list) if food_list else []

    return {
        "url": page_url,
        "title": title,
        "meal_type": meal_type,
        "date": date,
        "vegetarian_available": vegan_cb is not None,
        "vegetarian_selected": _is_checked(vegan_cb),
        "items": items,
        "item_count": len(items),
    }
