"""Read-only parsing and normalization for İTÜ help desk tickets."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from .parsing import clean_text, make_soup, normalize_lookup_text

HELP_BASE = "https://yardim.itu.edu.tr/"
SUMMARY_FIELDS = ("id", "title", "unit", "category", "status", "created_at", "updated_at", "url", "has_reply")
_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?")


def ticket_id(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value if re.fullmatch(r"[0-9]{1,20}", value) else None


def ticket_url(identifier: str) -> str:
    return f"{HELP_BASE}itubilet.aspx?id={identifier}"


def help_url(value: Any, base: str = HELP_BASE) -> str | None:
    """Keep only HTTPS help desk links, upgrading legacy HTTP links."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(urljoin(base, value.strip()))
        if parsed.hostname != "yardim.itu.edu.tr" or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return None
        if parsed.scheme not in ("http", "https"):
            return None
        return urlunsplit(("https", "yardim.itu.edu.tr", parsed.path, parsed.query, ""))
    except ValueError:
        return None


def id_from_url(value: Any) -> str | None:
    url = help_url(value)
    if not url or urlsplit(url).path.lower() not in ("/bilet.aspx", "/itubilet.aspx"):
        return None
    ids = [v for k, values in parse_qs(urlsplit(url).query).items() if k.lower() == "id" for v in values]
    return ticket_id(ids[0]) if len(ids) == 1 else None


def date_value(value: Any) -> str | None:
    """Use local ISO timestamps without inventing a timezone or an exact age."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.isoformat() if " " in value else parsed.date().isoformat()
        except ValueError:
            pass
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _first(item: dict[str, Any], *keys: str) -> Any:
    return next((item[key] for key in keys if item.get(key) is not None and item[key] != ""), None)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return clean_text(str(value)) or None


def summary(item: dict[str, Any]) -> dict[str, Any]:
    raw_url = _first(item, "url", "URL", "Url", "Link")
    url = help_url(raw_url)
    identifier = id_from_url(url) or ticket_id(_first(item, "id", "TicketId", "ObjectId", "Id"))
    status = _text(_first(item, "status", "TicketStateName", "Status", "StatusName"))
    reply = _first(item, "has_reply", "HasReply")
    reply = reply if isinstance(reply, bool) else None
    result = {
        "id": identifier,
        "title": _text(_first(item, "title", "Title", "Subject")),
        "unit": _text(_first(item, "unit", "UnitName")),
        "category": _text(_first(item, "category", "CategoryName")),
        "status": status,
        "created_at": date_value(_first(item, "created_at", "CreateDate", "CreatedAt")),
        "updated_at": date_value(_first(item, "updated_at", "UpdateDate", "UpdatedAt")),
        "url": url or (ticket_url(identifier) if identifier else None),
        "has_reply": reply,
        "age": _text(_first(item, "age", "BeforeCreateDate", "date")),
    }
    # Preserve legacy widget fields. Relative age is separate from exact dates.
    for key in ("date", "archived"):
        if key in item:
            result[key] = item[key]
    return result


def parse_widget(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    items = []
    for li in soup.select('ul[data-placement="yardim-list"] li.help__list-item'):
        anchor = li.find("a", href=True)
        if anchor is None:
            continue
        title = anchor.select_one(".pull-left")
        age = anchor.select_one(".pull-right")
        if title is None:
            continue
        badge = title.select_one(".panel-red, .panel-grey, .panel-green, .label, .badge")
        status = badge.get_text(" ", strip=True) if badge else None
        if badge:
            badge.extract()
        title_text = title.get_text(" ", strip=True)
        if title_text:
            href = str(anchor["href"])
            resolved = href
            try:
                path = urlsplit(href).path.lower()
                if path not in ("/bilet.aspx", "/itubilet.aspx", "bilet.aspx", "itubilet.aspx"):
                    resolved = urljoin(page_url, href)
            except ValueError:
                resolved = ""
            item = summary({"title": title_text, "status": status, "url": resolved,
                            "date": age.get_text(" ", strip=True) if age else None,
                            "archived": normalize_lookup_text(status or "") == "arsiv"})
            # Preserve Portal links from older widget layouts as display-only URLs.
            if item["url"] is None and resolved.startswith("https://portal.itu.edu.tr/"):
                item["url"] = resolved
            items.append(item)
    return {"url": page_url, "count": len(items), "tickets": items}


def _attachments(node: Any, page_url: str) -> list[dict[str, Any]]:
    items = []
    for anchor in node.select("a[href]"):
        href = str(anchor["href"])
        try:
            path = urlsplit(href).path.lower()
        except ValueError:
            continue
        if not (anchor.has_attr("download") or re.search(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|png|jpe?g|txt)$", path)
                or re.search(r"(?:dosya|download|attachment)", path)):
            continue
        url = help_url(href, page_url)
        if url and not any(item["url"] == url for item in items):
            items.append({"name": clean_text(anchor.get_text(" ", strip=True)) or path.rsplit("/", 1)[-1], "url": url})
    return items


def parse_detail(html: str, page_url: str, expected_id: str) -> dict[str, Any]:
    """Parse the ticket information and chronological operations tables."""
    soup = make_soup(html)
    root = soup.select_one("#sub .results")
    if root is None:
        return {"parse_warning": "Help ticket detail layout was not found."}
    info = history = None
    for table in root.select("table"):
        header = table.find("th")
        label = normalize_lookup_text(header.get_text(" ", strip=True)) if header else ""
        if label == "yardim bileti bilgileri":
            info = table
        elif label == "yapilan islemler":
            history = table
    if info is None:
        return {"parse_warning": "Help ticket information table was not found."}
    labels = {"bilet no": "id", "ilgili birim": "unit", "kategori": "category",
              "alt kategori": "subcategory", "durumu": "status", "durum": "status",
              "olusturulma tarihi": "created_at", "son guncelleme": "updated_at"}
    fields: dict[str, Any] = {}
    for row in info.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        key = labels.get(normalize_lookup_text(cells[0].get_text(" ", strip=True)).rstrip(":"))
        if key:
            fields[key] = clean_text(cells[1].get_text(" ", strip=True)) or None
    if ticket_id(fields.get("id")) != expected_id:
        return {"parse_warning": "Help page did not identify the requested ticket."}
    heading = root.find("h1")
    fields["title"] = heading.get_text(" ", strip=True) if heading else None
    fields["url"] = help_url(page_url) or ticket_url(expected_id)
    ticket = summary(fields)
    ticket["subcategory"] = fields.get("subcategory")
    messages = []
    # The help desk renders newest operations first. Return chronological
    # history so the original request identifies the requester and description.
    for row in reversed(history.select("tbody tr")) if history else []:
        # Some archived pages omit a closing </td>, nesting the message cell.
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        author_tag = cells[0].find("strong")
        author = _text(author_tag.get_text(" ", strip=True)) if author_tag else None
        stamp_parts = [str(x) for x in cells[0].find_all(string=True)
                       if x.find_parent("td") is cells[0] and x.find_parent("strong") is None]
        match = _DATE.search(" ".join(stamp_parts))
        stamp = date_value(match.group(0)) if match else None
        body = make_soup(str(cells[1]))
        for unwanted in body.select("script, style, input, textarea, button, select"):
            unwanted.decompose()
        content = clean_text(body.get_text("\n", strip=True))
        # Status events have an icon in the author cell and a dated operator
        # attribution in the content cell. They update the ticket, not its reply.
        event_date = re.search(r"@\s*(" + _DATE.pattern + r")", content)
        is_event = stamp is None and event_date is not None and body.find("strong") is not None and "tarafindan" in normalize_lookup_text(content)
        if is_event:
            stamp = date_value(event_date.group(1))
        messages.append({"author": author, "created_at": stamp,
                         "content": content, "kind": "status_change" if is_event else "message",
                         "attachments": _attachments(body, page_url)})
    # The first operation is the request. Subsequent messages from that author
    # are follow-ups, not evidence of an institutional answer.
    requester = messages[0]["author"] if messages else None
    replies = []
    for index, message in enumerate(messages):
        is_reply = (index > 0 and bool(requester and message["author"])
                    and normalize_lookup_text(message["author"]) != normalize_lookup_text(requester)
                    and bool(message["content"]))
        if message["kind"] == "status_change":
            is_reply = False
            message["is_reply"] = False
        else:
            message["is_reply"] = is_reply if requester and message["author"] else None
        if is_reply:
            replies.append(message)
    ticket["description"] = messages[0]["content"] if messages else None
    ticket["messages"] = messages
    ticket["replies"] = replies
    ticket["institution_reply"] = replies[-1]["content"] if replies else None
    ticket["attachments"] = []
    for message in messages:
        for attachment in message["attachments"]:
            if attachment not in ticket["attachments"]:
                ticket["attachments"].append(attachment)
    ticket["has_reply"] = True if replies else (False if messages and all(m["is_reply"] is not None for m in messages) else None)
    ticket["created_at"] = ticket["created_at"] or (messages[0]["created_at"] if messages else None)
    ticket["updated_at"] = ticket["updated_at"] or (messages[-1]["created_at"] if messages else None)
    ticket["missing_fields"] = [key for key in SUMMARY_FIELDS if ticket.get(key) is None]
    if not messages:
        ticket["parse_warning"] = "Ticket history was unavailable. Reply and date fields may be incomplete."
    return ticket
