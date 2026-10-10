# Registration planning

These three read-only tools connect the saved OBS registration draft, official elective groups, current undergraduate sections, and the student's degree plan. They require no course registration or draft changes.

## Read the saved draft

Call `obs_get_registration_draft()` to read the active registration period's saved draft. Each course includes its CRN, code, name, saved eligibility, restriction reasons, and any available meeting times. `checked_at` is the last time OBS checked the saved draft, while `retrieved_at` is the time this tool read it.

`draft_exists: false` means OBS explicitly returned no draft. An unreadable response is an error. Optional calendar failures leave the course list available and appear in `errors`. `schedule_available: false` means usable meeting times were not returned, which does not establish that the course has no meetings.

Reading a draft does not refresh its saved verdict. Use the plan validator for a current independent check.

## Inspect an elective group

Call `obs_get_elective_group(group_id)` with the official group ID shown in the degree plan. Group IDs are not restricted to a particular student or program.

The result contains the official membership list and matches each exact course code, including its language suffix, to the current undergraduate schedule. Each member includes `offered_this_term`, `crns`, `sections`, and `eligibility`. Sections retain meeting times, capacity, program restrictions, and prerequisite information from the public table. Their individual `eligibility` records contain the authenticated OBS verdict and restriction codes.

`sections` and `crns` contain normal course offerings. Sections marked `Ek Sınav` are listed under `exam_only_sections` and `exam_only_crns`, and do not make `offered_this_term` true. Zero capacity by itself does not identify an exam section. Only normal sections are included in the group's independent eligibility checks.

Membership does not establish registration eligibility. A course can belong to an elective group and have an offered section that excludes the student's program. Missing, unreadable, or mismatched term data remains unknown.

Section eligibility is checked independently, so alternative electives do not create artificial conflicts or exceed a combined credit limit. Up to 8 sections are checked per call, because each check is an authenticated request followed by a two second pause and counts against the OBS draft-check quota. All membership and schedule rows are returned, and unchecked eligibility remains unknown. Check a specific remaining CRN with `obs_validate_registration_plan`. Public membership and schedules remain available if authentication fails.

## Validate a proposed plan

Call `obs_validate_registration_plan(crns)` with one to twelve unique CRN strings of four or five digits. For example, `{"crns": ["12345", "12346"]}` illustrates the input shape. Use real current CRNs obtained from the other tools.

The result reports:

- Independent OBS validation for the complete proposed selection, including enrollment holds, section quota, existing registrations, program restrictions, and credit limits.
- Meeting conflicts among selected sections, with unknown meeting times identified.
- Official prerequisite expressions with AND/OR alternatives, minimum grades, completed credit and class requirements.
- A possible assignment of selected courses to open elective slots, using each course at most once and preserving existing degree-plan completions.
- Current graduation progress, remaining required courses and elective slots, GPA, internship, and English-credit requirements.
- Future required-course chains that the selection preserves conditionally, and prerequisites that remain unmet when a course is deferred.

`status` is `valid`, `invalid`, or `incomplete`. `valid` is respectively `true`, `false`, or `null`. A known blocker produces `invalid`, even if other checks are incomplete. Missing evidence never produces an unconditional success.

Courses in this selection do not count as already completed prerequisites for another course in the same selection. Future chains and elective assignments are conditional on registration, passing, required grades, and official credit counting. A chain to a course requiring a minimum BB grade is preserved only if that grade is achieved. Unknown local grade evidence remains separate when the live OBS checker confirms eligibility.

Prerequisite tools share the full İTÜ numeric grade table, including all six `+` grades, and include successful valid unused courses in completed history. A nonnumeric pass does not establish a numeric minimum. Unverified grade evidence appears in `unknown_courses`, separately from known unmet requirements in `missing_courses`.

A valid registration plan does not certify graduation. Unfilled requirements can remain for later terms. Future course offerings, time to graduation, final GPA, and final credit counting are not predicted. Chain analysis covers remaining required courses in the student's official degree plan. It does not infer an exhaustive dependency graph for every possible elective.

## Sources and execution

The draft tool reads the OBS registration-period, saved-draft, and draft-calendar endpoints. Elective membership comes from the official public degree-plan group page. Section schedules and branch prerequisite tables come from public OBS pages. Student history and degree requirements come from the authenticated graduation endpoint.

The validator uses OBS's independent `POST /api/TaslakKontrolAPI/v1/` check, which the OBS interface exposes separately from saving a draft or registering courses. Calls sharing an OBS client are serialized and spaced to respect its short transaction lock. A reported busy transaction receives one delayed retry. Other errors and missing result rows remain visible as unknown data.

The tools do not invoke draft creation, draft deletion, saved-draft rechecking, course registration, or course dropping. Response text originating in OBS, such as course names and restriction details, retains its original language and is marked as external content. Tool descriptions and generated analysis are English.
