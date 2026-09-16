# Library service status

Checked on September 13, 2026.

The [official ITU Library website](https://kutuphane.itu.edu.tr/) links its catalog search to
[the current catalog](https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/),
which identifies itself as SirsiDynix Portfolio. This differs from the legacy Millennium
WebPAC platform implemented by `LibraryClient`. Modern records use `SD_ILS` identifiers,
and their routes and dynamically loaded copy statuses require a separate adapter.
Changing the base hostname alone is insufficient.

Live HTTPS requests to the legacy `divit.library.itu.edu.tr` catalog failed certificate
verification. The current catalog passed TLS verification but returned HTTP 403 from
the development machine, the deployed server, and a separate browser tab. These checks
did not use library credentials. Search, item details, availability, and account access
on the current platform therefore remain unverified. TLS verification stays enabled.

The availability fix addresses an independent, reproducible error in the existing client.
Previously, any copy field containing `available` could count as available, including
the status `UNAVAILABLE`. The client now reads recognized status fields and matches
explicit positive or negative statuses. Missing and unfamiliar statuses remain unknown.
An available copy proves availability, while a negative overall result requires every
copy to have an explicitly negative status. Existing fields remain present, with
additional counts for unavailable and unknown copies and warnings for incomplete evidence.

This fix is covered by mocked regression tests. It does not establish that the library
tools currently work against the migrated live catalog. A new adapter requires accessible
public catalog pages and verification of the separate library authentication flow.
