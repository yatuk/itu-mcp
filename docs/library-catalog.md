# İTÜ public library catalog

The default library client uses the current [Sirsi Portfolio catalog](https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/).
`library_search` reads public results and returns stable `SD_ILS:` record IDs,
title, author, ISBN, call number and canonical detail links. The optional `offset`
is a zero-based result offset. Pass `next_offset` to retrieve another page.
`total_count` is the catalog's reported total; `count` is the returned page size.
`has_more=null` means the total could not be established. Paging is bounded to an
offset of 10000; `pagination_limit_reached` distinguishes that bound from exhaustion.
A single-result query may redirect directly to a detail page.

`library_get_item` reads bibliographic fields and the public copy table.
The current catalog loads copy status with a separate asynchronous request. This
adapter does not execute that JavaScript or submit that request. It preserves the
copy location, barcode and call number and reports a pending status as unknown.
`library_check_availability` therefore returns `available=null` when this is the
only status evidence. The official catalog link is the source for current shelf
availability. Missing copy tables do not prove that an item has no copies.

`library_get_account` and `library_list_loans` read the student's own account
on the current catalog. The account is separate from Ninova: it signs in with
the university number and a library PIN (`NINOVA_LIBRARY_ID`,
`NINOVA_LIBRARY_PIN`; `NINOVA_LIBRARY_NAME` is only for the legacy platform).
The PIN goes only to the catalog's own sign-in form on
`katalog.kutuphane.itu.edu.tr`, through a private session that shares no
cookies with the anonymous public client. The tools read the account page and
return the status, loans and holds (title, `SD_ILS:` record ID, dates) and the
fines total. They never change anything. If the account reports more loans or
holds than could be read from the page, `parse_warning` says so.

Renewal and reservation are not implemented for Sirsi. `library_renew_loan` and
`library_reserve_item` fail before any request is sent, including when
`confirm=true` is supplied.

## Searching for sources: `library_search_databases`

`library_search_databases` lists the library's licensed article, journal and
e-book databases (about 250) for a topic. It reads the public JSON service
behind <https://kutuphane.itu.edu.tr/arastirma/veritabanlari> once, caches it
(`NINOVA_LIBRARY_DATABASES_CACHE_TTL_SECONDS`, six hours by default) and
searches it locally: a query matches the name, subjects, publisher and
description, with Turkish diacritics ignored, and databases that match more
words rank higher. `subject` narrows to one subject area, and a call with no
arguments returns the subject list. Each result carries the on-campus link and
the OpenAthens off-campus link. The tool finds databases to search in. It does
not search inside them, and it needs no credentials.

An explicitly configured legacy `https://divit.library.itu.edu.tr` base retains
the old WebPAC implementation for compatibility. Its operational availability is
not established. Old `b...` IDs cannot be translated to `SD_ILS:` by reusing their
numeric portion; search the current catalog to obtain its actual record ID.
Modern base URLs are restricted to the official root, with TLS verification and
redirect host validation enabled. HTTP 403, TLS errors, wrong item identity and
unrecognized search HTML remain errors rather than successful empty results.

## Articles: `search_articles`

`search_articles` searches individual journal articles in the open OpenAlex
index: title, authors, journal, year, abstract, DOI, citation count and an open
access link when one exists. Each result with a DOI also gets `itu_access_url`,
which goes through the library's OpenAthens redirector, so the article opens
with İTÜ's subscription in the student's browser. Only `api.openalex.org` is
called and no İTÜ credentials are used. Without a key, OpenAlex shares one
daily quota per IP address; `NINOVA_OPENALEX_API_KEY` gives the server its own.

## ProQuest: `proquest_search` (off by default)

`proquest_search` searches İTÜ's ProQuest subscription signed in with the
library account, the way a browser does: ProQuest's sign-in for Istanbul
Technical University opens İTÜ's OpenAthens form, which takes
`NINOVA_LIBRARY_ID` and `NINOVA_LIBRARY_PIN`, and a SAML response is posted
back to ProQuest.

It is off until the operator sets `NINOVA_PROQUEST_ENABLE=1` in the MCP client
configuration or an explicitly named env file; an auto-discovered `.env` cannot
turn it on. The licence covers the student's own research, so the tool is kept
small on purpose: one page of up to 20 results per call, at least three seconds
between searches, a daily cap (`NINOVA_PROQUEST_DAILY_SEARCHES`, 40 by default),
identical searches answered from a 30 minute cache without using the cap, and
no full-text download. Results carry `itu_url` links that open the record in the
browser. It is not offered on the remote HTTP transport.

The password is sent only to İTÜ's tenant on `login.openathens.net`
(`/auth/itu.edu.tr/`), the SAML response only to ProQuest's service provider,
and the search form only to `www.proquest.com`. Every request and redirect is
limited to those three hosts.

## Optional local proxy for public catalog traffic

An operator may set `NINOVA_LIBRARY_PROXY_URL=http://127.0.0.1:18888` to use an
existing HTTP CONNECT proxy. The URL must use literal `127.0.0.1` or `[::1]`, an
explicit port from 1024 to 65535, and no credentials, query, fragment or path
other than an optional trailing slash. The proxy must already permit CONNECT
to `katalog.kutuphane.itu.edu.tr:443`. This setting does not install a proxy or
change service privileges, VPN configuration, system routing or TLS checks.
The client makes one attempt per request and follows at most three redirects.
Connection failure does not switch to another proxy or a direct route.

Only anonymous public Sirsi GET requests without bodies use this transport.
Every request and redirect must use the exact official HTTPS host and a verified
catalog root, search-results or detail path, with bounded public search query
parameters. Account, login and Tapestry component-action routes are excluded.
CONNECT carries encrypted HTTPS, so path and method validation occurs in the
client; the operator's proxy remains responsible for its own destination policy.

The public client owns its session, including any session explicitly supplied
by embedding code. It clears inherited headers, cookies, authentication, client
certificates, parameters, hooks and proxy settings before use, and disables
environment proxy and `.netrc` discovery. This also applies with no proxy option,
when requests use the system's direct route. Anonymous cookies issued by the
catalog after initialization are retained for its verified redirects.
Legacy WebPAC sessions and other MCP clients keep their existing transports.

## Validation and remaining deployment requirement

On 16 September 2026, anonymous live reads through the already configured Pi
Ethernet source plus `mullvad-exclude` verified:

- `search/results?qu=thermodynamics&ps=12` returned 5921 results, starting with
  `SD_ILS:69360`, *Thermodynamics*, ISBN `9780070682856`.
- Adding `rw=12` returned result 13 onward with distinct record IDs.
- A nonexistent query returned the explicit `#searchResultText` empty-result marker.
- `qu=9780070682856&rt=false|||ISBN|||ISBN` redirected to the same public record.
  The other field codes come from the current catalog's search selector. They
  have fixture-level parameter coverage but were not individually live-tested.
- The canonical detail URL returned the matching bibliography and one public
  copy, with its status still loading in the initial HTML.

The four `library_sirsi_*.html` fixtures contain the relevant DOM excerpts from
those anonymous responses. Scripts, account forms and session tokens are removed.
Fixture tests cover parsing, identity, paging, redirects, transport errors and
unavailable account operations. They do not demonstrate live account or shelf
availability support.

The default Pi request path returned HTTP 403. A source-address binding alone
failed to connect, as did `mullvad-exclude` without the source binding. Only their
existing combination returned HTTP 200. The probe ran as the SSH user, while the
production service runs as `itu-mcp`. The optional loopback proxy adds no transport
subprocess, source binding or TLS exemption to the library client. Its focused
offline tests inspect actual prepared requests, anonymous cookies, destination
checks and failure handling. Production access through an operator-configured
proxy must be separately verified in the service's actual execution context.
The adapter's live acceptance is limited to the established working public
transport. It is not acceptance of the complete Library tool group.
