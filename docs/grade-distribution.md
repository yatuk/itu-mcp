# Historical grade distributions

`obs_get_grade_distribution(course_code, year=None, term_code=None)` reads the counts and percentages published in the public OBS grade chart. It requires no account credentials.

For example, `{"course_code": "UZB 438E", "year": 2026}` returns the published terms for the 2025–2026 academic year. The `year` parameter is the academic ending year and defaults to the current calendar year in Turkey. It does not automatically search earlier years. Use a `term_code` from `available_terms` to select one term. When `year` is omitted, that code determines the year.

Each term includes `counts_by_grade`, detailed `grades` with percentages, the announced student total, the sum of the published counts, and a consistency flag. All published grade labels are preserved, including plus grades. Missing grades are not silently added with zero counts.

OBS can combine language variants, such as UZB438 and UZB438E, into one distribution. `reported_course_codes` identifies the actual aggregate. The result does not infer separate counts for a language, CRN, section, or instructor.

The tool reads the JSON used to render the chart rather than the empty HTML table before JavaScript runs. It never evaluates page JavaScript. Missing or malformed chart data produces an error. An explicit no-data response or an unavailable selected term returns `available: false`; that does not establish that nobody enrolled. A published zero total remains distinct from missing data.

Published totals and percentages are checked against the grade counts. A discrepancy produces `status: incomplete` and a warning while preserving the original figures. The tool does not estimate omitted grades or predict how difficult the course will be in a later term.

Source: [OBS grade distribution](https://obs.itu.edu.tr/public/DersNotDagilimi), using its `NotDagilimiSearch` result endpoint.
