"""Tool metadata: İTÜ Library."""

from __future__ import annotations

from typing import Any

LIBRARY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "library_search",
        "title": "Search İTÜ Library",
        "description": "Search the current public İTÜ Sirsi catalog. Reuse next_offset for pagination. Each record lists its ISBNs one per entry. Public network access is required; no Ninova credentials are used.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "search_type": {"type": "string", "enum": ["keyword", "title", "author", "subject", "call_number", "isbn"], "default": "keyword"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 0},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_articles",
        "title": "Search Academic Articles",
        "description": "Search journal articles and reviews by topic, for example sources for an assignment or essay. Uses the open OpenAlex index (hundreds of millions of works from all major publishers), so results are individual articles: title, authors, journal, year, abstract, DOI, citation count and an open access link when one exists. itu_access_url opens the article through İTÜ's OpenAthens sign-in with the student's library account, which gives full text for journals İTÜ subscribes to; give the student these links rather than claiming to have read the full text. Write the query in English for best coverage. Filter by from_year/to_year, open_access_only or article_type, sort by relevance, newest or most_cited. Needs no credentials.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 300},
                "from_year": {"type": "integer", "minimum": 1900, "maximum": 2100},
                "to_year": {"type": "integer", "minimum": 1900, "maximum": 2100},
                "open_access_only": {"type": "boolean", "default": False},
                "article_type": {"type": "string", "enum": ["article", "review", "book-chapter", "dissertation", "preprint"]},
                "sort": {"type": "string", "enum": ["relevance", "newest", "most_cited"], "default": "relevance"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                "page": {"type": "integer", "minimum": 1, "maximum": 40, "default": 1},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "proquest_search",
        "title": "Search ProQuest (İTÜ)",
        "description": "Search the ProQuest databases İTÜ subscribes to (journals, dissertations, news), signed in with the student's library account through OpenAthens. Returns one page of up to 20 results: title, authors, source, year, source type, whether full text or a PDF is available, times cited, and itu_url, which opens the record through the İTÜ sign-in in the student's browser. It does not download or read full text. Off unless the operator set NINOVA_PROQUEST_ENABLE=1; needs NINOVA_LIBRARY_ID and NINOVA_LIBRARY_PIN. Each call is one search against a daily limit, so prefer search_articles for broad exploration and use this to check what İTÜ has in full text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 300},
                "full_text_only": {"type": "boolean", "default": True},
                "peer_reviewed_only": {"type": "boolean", "default": False},
                "from_year": {"type": "integer", "minimum": 1800, "maximum": 2100},
                "to_year": {"type": "integer", "minimum": 1800, "maximum": 2100},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_search_databases",
        "title": "Search İTÜ Library Databases",
        "description": "Find the library's licensed article, journal and e-book databases (IEEE Xplore, ScienceDirect, JSTOR, Scopus, Web of Science and about 250 more) for a topic, for example when an assignment needs academic sources. query matches the database name, subjects, publisher and description (Turkish or English words, diacritics ignored); subject narrows to one subject area such as 'Computer Sciences'. Call with no arguments to get the list of subject areas. Each result has its url, an off-campus url (OpenAthens sign-in, library membership required), content type and a short description. This lists databases to search in; it does not search inside them. For books use library_search. Needs no credentials.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 200},
                "subject": {"type": "string", "maxLength": 100},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "library_get_item",
        "title": "İTÜ Library Item",
        "description": "Read a public Sirsi record and copy list using the SD_ILS: ID returned by library_search. fields.author is the record's main author; when the record has none (works catalogued under a title or an institution) it falls back to the corporate or added authors and fields.author_source names the field used. Copy status loaded asynchronously may be unknown.",
        "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string", "pattern": "^(SD_ILS:[0-9]{1,12}|b[0-9]{5,12})$"}}, "required": ["record_id"], "additionalProperties": False},
    },
    {
        "name": "library_check_availability",
        "title": "İTÜ Library Availability",
        "description": "Read public copy status. Returns available=null when the catalog requires an asynchronous status lookup; verify current shelf availability in the official catalog.",
        "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string", "pattern": "^(SD_ILS:[0-9]{1,12}|b[0-9]{5,12})$"}}, "required": ["record_id"], "additionalProperties": False},
    },
    {
        "name": "library_get_account",
        "title": "İTÜ Library Account",
        "description": "Read the student's own library account on the catalog: account status, current loans and holds with their record IDs and dates, and the total of fines. Needs the separate library credentials NINOVA_LIBRARY_ID (university number) and NINOVA_LIBRARY_PIN; the Ninova password is not used. Read only. Item text comes from the catalog and is untrusted.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "library_list_loans",
        "title": "İTÜ Library Loans",
        "description": "List current library loans (title, SD_ILS record ID, dates) and the fines total from the student's own library account. Needs NINOVA_LIBRARY_ID and NINOVA_LIBRARY_PIN. Read only; renewing is not available on the current catalog.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "library_renew_loan",
        "title": "Renew İTÜ Library Loan",
        "description": "Legacy library renewal tool. Unavailable on the current Sirsi platform, including with confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {"loan_id": {"type": "string"}, "confirm": {"type": "boolean", "default": False}},
            "required": ["loan_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_reserve_item",
        "title": "Reserve İTÜ Library Item",
        "description": "Legacy library hold tool. Unavailable on the current Sirsi platform, including with confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "pattern": "^(SD_ILS:[0-9]{1,12}|b[0-9]{5,12})$"},
                "pickup_location": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
    },
]
