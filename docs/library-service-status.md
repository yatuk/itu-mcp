# Library service status

Updated on September 16, 2026.

The [official ITU Library website](https://kutuphane.itu.edu.tr/) links its catalog search to
[the current catalog](https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/),
which identifies itself as Sirsi Portfolio. `LibraryClient` now defaults to that
platform with a verified public GET adapter for searches, `SD_ILS:` record identity,
detail pages and copy tables. See [catalog support](library-catalog.md) for the live
routes, fixture provenance, pagination and remaining limitations.

Live HTTPS requests to the legacy `divit.library.itu.edu.tr` catalog failed certificate
verification. The current catalog passed TLS verification but returned HTTP 403 from
the ordinary development and deployed-server paths. On September 16, anonymous Pi
requests using the existing Ethernet source and `mullvad-exclude` together returned
HTTP 200 and verified search, detail, pagination and an explicit empty result. Source
binding alone was insufficient. This probe used the SSH user, not the production
service identity. No network or service configuration was changed.

Current shelf availability is still unknown when the initial HTML contains asynchronous
loading placeholders. The follow-up public POST was not accepted in the anonymous
probe. Account and loan flows were not tested and are explicitly unsupported by the
new adapter. No credentials were read or submitted. TLS verification stays enabled.

The availability fix addresses an independent, reproducible error in the existing client.
Previously, any copy field containing `available` could count as available, including
the status `UNAVAILABLE`. The client now reads recognized status fields and matches
explicit positive or negative statuses. Missing and unfamiliar statuses remain unknown.
An available copy proves availability, while a negative overall result requires every
copy to have an explicitly negative status. Existing fields remain present, with
additional counts for unavailable and unknown copies and warnings for incomplete evidence.

These behaviors are covered by fixture and mocked regression tests. Production
transport needs separate verification in the service's execution context. Successful
public GETs through the established test route do not establish that the complete
Library tool group is operational.
