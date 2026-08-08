"""Static reference tables exposed as MCP resources.

Only genuinely constant lookup data belongs here — no network, no
credentials, no per-student state. Everything else this server knows is
either a live query or requires a login, which makes it a tool rather than a
resource.

Both tables published here exist to stop callers from guessing: the letter
grade coefficients so a model does not invent İTÜ's 4.00 scale, and the
program-type codes because an invalid ``program_type`` is otherwise only
discoverable by triggering an error.
"""

from __future__ import annotations

from typing import Any

from .gpa import LETTER_TO_GRADE

# Mirrors the bands calculate_gpa() uses to annotate a computed GANO.
GPA_BANDS: list[dict[str, Any]] = [
    {"min_gpa": 3.50, "label": "Yüksek onur derecesinde."},
    {"min_gpa": 3.00, "label": "Onur derecesinde."},
    {"min_gpa": 2.00, "label": "Geçer seviyede (2.00 üstü)."},
    {"min_gpa": 1.80, "label": "Uyarı seviyesinde (2.00 altı); notları yükseltmeye bak."},
    {"min_gpa": 0.00, "label": "Kritik seviyede (1.80 altı); akademik uyarı riski var."},
]

GRADE_SCALE_URI = "itu://reference/grade-scale"
PROGRAM_TYPES_URI = "itu://reference/program-types"


def grade_scale() -> dict[str, Any]:
    """İTÜ 4.00 letter-grade coefficients, GPA bands, and failing grades."""
    counted = {code: value for code, value in LETTER_TO_GRADE.items() if value is not None}
    excluded = sorted(code for code, value in LETTER_TO_GRADE.items() if value is None)
    return {
        "scale": "4.00",
        "coefficients": counted,
        "excluded_from_gpa": excluded,
        "excluded_note": (
            "Bu notlar ağırlıklı ortalamaya katılmaz (GE/KF/IA/NA/TR/MU/EK). "
            "Kredi sayılıp sayılmadığı nota göre değişir."
        ),
        "failing_grades": ["FF", "VF"],
        "failing_note": (
            "FF ve VF katsayı 0.00 ile ortalamaya girer ve dersin tekrar "
            "alınması gerekir. VF devamsızlıktan kalmadır."
        ),
        "gpa_bands": GPA_BANDS,
        "source": "İTÜ lisans yönetmeliği harf notu sistemi",
        "note": (
            "Bilgi amaçlıdır; resmî GANO için OBS transkriptine bakın. "
            "Lisansüstü notlandırması (BL/BZ) farklıdır."
        ),
    }


def program_types() -> dict[str, Any]:
    """Valid program_type values accepted by the public OBS schedule tools."""
    # Imported lazily: obs_client pulls in requests and the parsing stack, and
    # a static reference table should not drag that in at module import time.
    from .obs_client import ObsPublicClient

    aliases = dict(ObsPublicClient.PROGRAM_TYPE_MAP)
    canonical = sorted(set(aliases.values()))
    return {
        "canonical_values": canonical,
        "descriptions": {
            "LS": "Lisans",
            "LU": "Lisansüstü (yüksek lisans / doktora)",
            "ÖL": "Önlisans",
            "LUİ": "Lisansüstü 2. öğretim",
        },
        "aliases": aliases,
        "alias_note": (
            "Alias eşleşmesi büyük/küçük harf ve Türkçe aksan duyarsızdır; "
            "kanonik değerler de doğrudan kabul edilir."
        ),
        "used_by": [
            "get_public_course_schedule",
            "find_open_course_sections",
            "find_empty_classrooms",
            "check_course_conflicts",
            "explain_course_eligibility",
        ],
        "default": "LS",
    }


# Metadata mirrors TOOLS/PROMPTS so every transport registers one surface.
RESOURCES: list[dict[str, Any]] = [
    {
        "uri": GRADE_SCALE_URI,
        "name": "itu-grade-scale",
        "title": "İTÜ Harf Notu Sistemi",
        "description": (
            "İTÜ 4.00 letter-grade coefficients, which grades are excluded from "
            "the GPA, failing grades, and the GPA comment bands."
        ),
        "builder": grade_scale,
    },
    {
        "uri": PROGRAM_TYPES_URI,
        "name": "itu-program-types",
        "title": "OBS Program Tipleri",
        "description": (
            "Valid program_type values (LS/LU/ÖL/LUİ) accepted by the public OBS "
            "schedule and planning tools, with their accepted aliases."
        ),
        "builder": program_types,
    },
]

RESOURCE_URIS: list[str] = [resource["uri"] for resource in RESOURCES]
