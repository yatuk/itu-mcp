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

Account, loan, renewal and reservation support has not been implemented for
Sirsi. The existing tool names remain discoverable with explicit unavailable
descriptions. On this platform they fail before reading account credentials or
submitting an account action, including when `confirm=true` is supplied.

An explicitly configured legacy `https://divit.library.itu.edu.tr` base retains
the old WebPAC implementation for compatibility. Its operational availability is
not established. Old `b...` IDs cannot be translated to `SD_ILS:` by reusing their
numeric portion; search the current catalog to obtain its actual record ID.
Modern base URLs are restricted to the official root, with TLS verification and
redirect host validation enabled. HTTP 403, TLS errors, wrong item identity and
unrecognized search HTML remain errors rather than successful empty results.

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
