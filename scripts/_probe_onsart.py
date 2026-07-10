from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ninova_mcp.client import NinovaClient
from ninova_mcp.env import load_ninova_env


def main() -> None:
    load_ninova_env()
    client = NinovaClient()
    client.ensure_logged_in()
    s = client.session
    base = "https://obs.itu.edu.tr/public/GenelTanimlamalar/DersOnsartList"
    r = s.get(base, timeout=30)
    print("page", r.status_code, len(r.text))
    for match in re.findall(r'["\'](/[^"\']*(?:Onsart|onsart|DersBilgi)[^"\']*)["\']', r.text):
        print("path", match)
    for match in re.findall(r'["\'](https?://[^"\']*Onsart[^"\']*)["\']', r.text, flags=re.I):
        print("url", match)

    soup = BeautifulSoup(r.text, "lxml")
    # try change event URL patterns from inline scripts
    scripts = "\n".join(t.get_text() for t in soup.find_all("script") if t.get_text())
    print("inline script len", len(scripts))
    for m in re.finditer(r".{0,40}Onsart.{0,80}", scripts, flags=re.I):
        print("CTX", m.group(0)[:120])

    # POST as form-urlencoded with submit button
    sel = soup.find("select", id="DersBransKoduId") or soup.find("select", attrs={"name": "DersBransKoduId"})
    token = soup.find("input", attrs={"name": "__RequestVerificationToken"})
    data = {
        "DersBransKoduId": "304",
    }
    if token:
        data["__RequestVerificationToken"] = token.get("value") or ""
    # also try button names
    for name in ("button", "btn", "submit", "Submit"):
        data_try = dict(data)
        r2 = s.post(base, data=data_try, headers={"Referer": base, "X-Requested-With": "XMLHttpRequest"}, timeout=30)
        print("post simple", r2.status_code, r2.headers.get("content-type"), len(r2.text), r2.text[:100].replace("\n", " "))

    # GET with route segments used elsewhere on OBS public
    for u in [
        "https://obs.itu.edu.tr/public/GenelTanimlamalar/DersOnsartList/304",
        "https://obs.itu.edu.tr/public/GenelTanimlamalar/DersOnsartDetay?dersBransKoduId=304",
        "https://obs.itu.edu.tr/public/GenelTanimlamalar/DersOnsartListesi?DersBransKoduId=304",
        "https://obs.itu.edu.tr/public/DersBilgi/Search?searchText=CEN%20311",
        "https://obs.itu.edu.tr/public/DersBilgi",
    ]:
        rr = s.get(u, timeout=20)
        print("get", rr.status_code, u, len(rr.content), rr.text[:90].replace("\n", " "))

    # DersBilgi page form
    db = s.get("https://obs.itu.edu.tr/public/DersBilgi", timeout=30)
    print("dersbilgi", db.status_code, len(db.text))
    for m in re.finditer(r".{0,30}(api|Onsart|DersKod).{0,60}", db.text, flags=re.I):
        if "script" in m.group(0).lower():
            continue
        print("DB", m.group(0)[:100])
        if m.start() > 5000:
            break


if __name__ == "__main__":
    main()
