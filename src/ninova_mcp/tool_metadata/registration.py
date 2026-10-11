"""Tool metadata: OBS course registration."""

from __future__ import annotations

from typing import Any

REGISTRATION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_get_registration_status",
        "title": "OBS Registration Status",
        "description": "Read official OBS registration status, class/GPA provenance and published class registration windows. Personal holds and credit limits are reported only when verified.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obs_get_registration_draft",
        "title": "OBS Registration Draft",
        "description": (
            "Read the saved OBS registration draft with CRNs, course names, saved eligibility "
            "verdicts, restriction reasons, last check time and available draft meeting times. "
            "Reads the active registration term. Does not save or recheck the draft. "
            "Saved eligibility is a snapshot; use obs_validate_registration_plan for a fresh check."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obs_save_registration_draft",
        "title": "OBS Save Registration Draft",
        "description": (
            "Replace the saved OBS registration draft with exactly one to twelve supplied CRNs. "
            "This changes the user's saved draft and can replace its existing contents. Requires "
            "the user's explicit confirmation of the exact CRNs and replacement, confirm=true, "
            "and the operator's NINOVA_OBS_REGISTRATION_WRITES=1 opt-in. Does not register or "
            "drop courses. If the save would remove courses already in the draft, nothing is "
            "sent and the result lists previous_crns and removed_crns; call again with "
            "allow_replace=true only after the user accepts losing them. Reports saved, "
            "unchanged, rejected or uncertain based on the submission and fresh draft readback. "
            "An uncertain result is not proof that the draft was saved. Local stdio only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "crns": {
                    "type": "array", "items": {"type": "string", "pattern": "^[0-9]{4,5}$"},
                    "minItems": 1, "maxItems": 12, "uniqueItems": True,
                },
                "confirm": {
                    "type": "boolean",
                    "description": "True only after the user explicitly confirms replacing the draft with these exact CRNs.",
                },
                "allow_replace": {
                    "type": "boolean", "default": False,
                    "description": "True only after the user has seen which draft courses would be removed and accepts it.",
                },
            },
            "required": ["crns", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_elective_group",
        "title": "OBS Elective Group",
        "description": (
            "Read any official elective group by group_id, including eligible course membership, "
            "current undergraduate offerings, CRNs, meeting times and student-specific section "
            "eligibility. Each section is checked independently, up to 8 sections per call. "
            "Membership and schedules are public; eligibility requires an OBS session. "
            "Unknown source data stays unknown. Does not change the saved draft or registration."
        ),
        "inputSchema": {"type": "object", "properties": {
            "group_id": {"type": "integer", "minimum": 1, "description": "Official OBS elective group ID from a degree plan."},
        }, "required": ["group_id"], "additionalProperties": False},
    },
    {
        "name": "obs_validate_registration_plan",
        "title": "OBS Validate Registration Plan",
        "description": (
            "Validate one to twelve proposed CRNs using the independent OBS CRN checker, public "
            "meeting times, official prerequisite rules and the student's completed course history. "
            "Reports time conflicts, program/credit/class restrictions, possible elective-slot "
            "assignments, remaining graduation requirements and prerequisite-chain deferral risks. "
            "Planned courses count toward future chains only conditionally on passing, never as "
            "already completed prerequisites. Returns valid, invalid or incomplete. Future course "
            "offerings and final graduation are not guaranteed. Does not save a draft or register courses."
        ),
        "inputSchema": {"type": "object", "properties": {
            "crns": {"type": "array", "items": {"type": "string", "pattern": "^[0-9]{4,5}$"},
                     "minItems": 1, "maxItems": 12, "uniqueItems": True},
        }, "required": ["crns"], "additionalProperties": False},
    },
]
