"""Watch public OBS course sections for a free seat.

A student keeps a short list of CRNs. ``check_watched_course_sections`` reads
the public course schedule once per branch, compares every watched section
with the previous observation, and reports what changed: a full section that
opened, an open one that filled, a raised capacity, a section that is no
longer listed.

Three rules shape everything here.

* **Nothing runs in the background.** An MCP server only acts when a client
  calls a tool. A check happens when the user, or a task scheduled on the
  client side, calls the check tool. There is no thread and no timer.
* **Unknown is not zero.** A missing capacity or enrolled value, or a branch
  whose enrolled column is 0 in every row because OBS has not filled it yet,
  is reported as ``unknown``. A seat is only reported as opened when both the
  earlier and the current observation carry real numbers.
* **CRNs are reused across terms.** Every entry stores the term it was added
  in. An entry from another term is never compared with the current schedule.

The tool methods live in a mixin and their MCP metadata in
``SEAT_WATCH_TOOLS`` so that ``app.py`` and ``tool_registry.py`` only have to
wire them in.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .obs_client import ObsError, ObsPublicClient
from .parsing import normalize_lookup_text
from .private_io import write_private_text

SEAT_WATCH_STATE_VERSION = 1
SEAT_WATCH_STATE_FILENAME = "seat-watch-state.json"

# One check costs one OBS request per distinct branch, so the worst case for a
# full list is this many requests. A student registers for six to eight courses
# and may watch a couple of alternative sections for each, which fits. It also
# stays under the 25 branch ceiling find_open_course_sections already applies
# to a single call.
MAX_WATCHED_SECTIONS = 20

# Statuses that carry real capacity and enrolled numbers.
KNOWN_STATUSES = frozenset({"open", "full", "no_capacity"})
# Among those, the ones with no seat to take.
CLOSED_STATUSES = frozenset({"full", "no_capacity"})
SEAT_CHANGES = frozenset({
    "opened", "filled", "capacity_raised", "capacity_lowered",
    "seats_changed", "enrollment_changed",
})

# Archive level labels (search.json) to the OBS program type keys.
_ARCHIVE_LEVELS = {"LS": "LS", "LU": "LU", "LUI": "LUİ", "LUİ": "LUİ", "OL": "ÖL", "ÖL": "ÖL"}
# get_active_semester substitutes this label when OBS sends no term.
_UNKNOWN_TERM_LABEL = "Bilinmeyen Dönem"

NO_BACKGROUND_NOTE = (
    "Sunucu arka planda çalışmaz ve kendiliğinden bildirim göndermez. Kontenjan yalnızca "
    "check_watched_course_sections çağrıldığında okunur; düzenli kontrol için bu aracı "
    "istemci tarafındaki zamanlanmış bir görev çağırmalıdır."
)

_STATE_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def term_key(label: Any) -> str:
    """Fold a term label so ``2026-2027 Güz Dönemi`` compares reliably."""
    return normalize_lookup_text(str(label)) if label else ""


def _usable_term(label: Any) -> bool:
    return bool(term_key(label)) and term_key(label) != term_key(_UNKNOWN_TERM_LABEL)


# -- state -------------------------------------------------------------------


def empty_seat_watch_state() -> dict[str, Any]:
    return {"version": SEAT_WATCH_STATE_VERSION, "updated_at": None, "watches": {}}


def load_seat_watch_state(path: Path) -> tuple[dict[str, Any], str | None]:
    """Read the watch list. Never raises: a bad file yields an empty list and a warning."""
    if not path.exists():
        return empty_seat_watch_state(), None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Same failure mode load_tracking_state tolerates: a write killed
        # mid-flight, or a file edited by hand into invalid JSON.
        return empty_seat_watch_state(), (
            "İzleme dosyası okunamadı (bozuk veya yarım yazılmış); izleme listesi boş "
            "kabul edildi. Dosya bir sonraki değişiklikte yeniden yazılır."
        )
    if not isinstance(document, dict) or not isinstance(document.get("watches"), dict):
        return empty_seat_watch_state(), (
            "İzleme dosyası beklenen biçimde değil; izleme listesi boş kabul edildi."
        )
    watches: dict[str, dict[str, Any]] = {}
    dropped = 0
    for key, entry in document["watches"].items():
        crn = entry.get("crn") if isinstance(entry, dict) else None
        if not isinstance(crn, str) or not crn.isascii() or not crn.isdigit() or key != crn:
            dropped += 1
            continue
        for field in ("last_observation", "last_known", "last_change"):
            if not isinstance(entry.get(field), dict):
                entry[field] = None
        watches[crn] = entry
    version = document.get("version")
    warning = None
    if type(version) is int and version > SEAT_WATCH_STATE_VERSION:
        warning = (
            f"İzleme dosyası daha yeni bir sürümle yazılmış (version {version}); "
            "bilinen alanlar okundu."
        )
    elif dropped:
        warning = f"İzleme dosyasındaki {dropped} geçersiz kayıt yok sayıldı."
    return {
        "version": SEAT_WATCH_STATE_VERSION,
        "updated_at": document.get("updated_at"),
        "watches": watches,
    }, warning


def save_seat_watch_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic and owner-only, the same helper as ``save_tracking_state``."""
    state["version"] = SEAT_WATCH_STATE_VERSION
    state["updated_at"] = _now_iso()
    write_private_text(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))


# -- reading one schedule ----------------------------------------------------


def _is_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def enrollment_populated(courses: list[dict[str, Any]]) -> bool:
    """True when at least one section of the branch reports a non-zero enrolled count.

    Before registration opens OBS lists every section with ``enrolled`` 0. That
    column then says nothing about free seats, so a branch where no section has
    anyone enrolled is treated as not populated yet.
    """
    return any(_is_count(course.get("enrolled")) and course["enrolled"] > 0 for course in courses)


def schedule_usable(schedule: dict[str, Any]) -> bool:
    """A schedule that can prove a CRN is absent: parsed cleanly and not empty."""
    return bool(schedule.get("courses")) and not schedule.get("parse_warning")


def observe_section(course: dict[str, Any] | None, *, populated: bool) -> dict[str, Any]:
    """Turn one schedule row (or its absence) into an observation."""
    if course is None:
        return {"status": "not_listed", "capacity": None, "enrolled": None, "seats_free": None}
    capacity = course.get("capacity")
    enrolled = course.get("enrolled")
    observation: dict[str, Any] = {
        "status": "unknown",
        "capacity": capacity if _is_count(capacity) else None,
        "enrolled": enrolled if _is_count(enrolled) else None,
        "seats_free": None,
    }
    if not _is_count(capacity):
        observation["unknown_reason"] = "capacity_missing"
    elif not _is_count(enrolled):
        observation["unknown_reason"] = "enrolled_missing"
    elif not populated:
        # The 0 is a placeholder here, so it is not passed on as a count.
        observation["enrolled"] = None
        observation["unknown_reason"] = "enrollment_not_populated"
    else:
        free = capacity - enrolled
        observation["seats_free"] = max(free, 0)
        if free < 0:
            observation["over_capacity"] = -free
        if capacity == 0:
            observation["status"] = "no_capacity"
        else:
            observation["status"] = "open" if free > 0 else "full"
    return observation


def _figures(observation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not observation:
        return None
    return {
        key: observation.get(key)
        for key in ("status", "capacity", "enrolled", "seats_free", "observed_at")
    }


def diff_observation(
    previous: dict[str, Any] | None,
    last_known: dict[str, Any] | None,
    current: dict[str, Any],
) -> list[str]:
    """Name what changed. Seat transitions need real numbers on both sides.

    ``previous`` is the last stored observation of any status; ``last_known``
    is the last one with real numbers. A seat transition compares ``current``
    with ``last_known``, so a check that could not read the numbers in between
    neither hides a real change nor invents one.
    """
    status = current["status"]
    if previous is None:
        return ["baseline"]
    previous_status = previous.get("status")
    changes: list[str] = []
    if status == "not_listed":
        return [] if previous_status == "not_listed" else ["delisted"]
    if previous_status == "not_listed":
        changes.append("relisted")
    if status == "unknown":
        if previous_status in KNOWN_STATUSES:
            changes.append("data_unavailable")
        return changes
    if last_known is None:
        # First real numbers for this section: a baseline, not a seat opening.
        return [*changes, "baseline"]
    if previous_status == "unknown":
        changes.append("data_available")
    before_status = last_known.get("status")
    if before_status in CLOSED_STATUSES and status == "open":
        changes.append("opened")
    elif before_status == "open" and status in CLOSED_STATUSES:
        changes.append("filled")
    before_capacity, capacity = last_known.get("capacity"), current["capacity"]
    capacity_moved = _is_count(before_capacity) and capacity != before_capacity
    if capacity_moved:
        changes.append("capacity_raised" if capacity > before_capacity else "capacity_lowered")
    if not {"opened", "filled"} & set(changes):
        if current["seats_free"] != last_known.get("seats_free"):
            changes.append("seats_changed")
        elif current["enrolled"] != last_known.get("enrolled") and not capacity_moved:
            changes.append("enrollment_changed")
    return changes


def _course_fields(course: dict[str, Any]) -> dict[str, Any]:
    return {
        "course_code": course.get("code"),
        "course_name": course.get("name"),
        "instructor": course.get("instructor"),
    }


def _entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get(key)
        for key in (
            "crn", "course_code", "course_name", "instructor",
            "department_code", "program_type", "term", "added_at",
        )
    }


def _stale_result(entry: dict[str, Any], active_term: str) -> dict[str, Any]:
    return {
        **_entry_summary(entry),
        "status": "stale_term",
        "active_term": active_term,
        "changes": [],
        "last_observation": entry.get("last_observation"),
        "note": (
            f"Bu kayıt {entry.get('term')!r} dönemine ait; aktif dönem {active_term!r}. "
            "Aynı CRN artık başka bir şubeyi gösterebilir, bu yüzden karşılaştırılmadı."
        ),
    }


def _skipped_result(entry: dict[str, Any], status: str, note: str) -> dict[str, Any]:
    return {
        **_entry_summary(entry),
        "status": status,
        "changes": [],
        "last_observation": entry.get("last_observation"),
        "note": note,
    }


def _observation_note(observation: dict[str, Any], changes: list[str], repeat: bool) -> str | None:
    status = observation["status"]
    if status == "not_listed":
        return (
            "CRN bölümün güncel programında yok (kaldırılmış veya başka bölüme taşınmış "
            "olabilir). Bu 'dolu' demek değildir."
        )
    if status == "unknown":
        if observation.get("unknown_reason") == "enrollment_not_populated":
            return (
                "Bölümdeki hiçbir şubede yazılan öğrenci görünmüyor; OBS 'Yazılan' sütununu "
                "henüz doldurmamış olabilir. Boş yer sayısı bilinmiyor, 'yer açıldı' sayılmaz."
            )
        return "OBS bu şube için kontenjan veya yazılan sayısını vermedi; boş yer sayısı bilinmiyor."
    if repeat:
        return "Son kontrolle aynı OBS okuması; yeni bir karşılaştırma yapılmadı."
    if changes == ["baseline"]:
        return "İlk gözlem. Karşılaştırma tabanı kuruldu, değişiklik bildirilmez."
    if status == "no_capacity":
        return "Kontenjan 0 olarak ilan edilmiş; alınabilecek yer yok."
    return None


# -- tools -------------------------------------------------------------------


def _crn_strings(crns: Any) -> Any:
    """A CRN sent as a JSON number is the same CRN; everything else is validated as given."""
    if not isinstance(crns, list):
        return crns
    return [str(crn) if isinstance(crn, int) and not isinstance(crn, bool) else crn for crn in crns]


class SeatWatchToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies the clients and ``state_dir``."""

    state_dir: Path
    obs_public: ObsPublicClient
    archive: Any

    @property
    def seat_watch_state_path(self) -> Path:
        return self.state_dir / SEAT_WATCH_STATE_FILENAME

    def _seat_watch_active_term(self, program_type: str) -> str | None:
        try:
            label = self.obs_public.get_active_semester(program_type)
        except ObsError:
            return None
        return str(label) if _usable_term(label) else None

    def _seat_watch_discover(
        self, crns: list[str], program_type: str | None
    ) -> tuple[dict[tuple[str, str], list[str]], list[dict[str, Any]]]:
        """Map CRNs to ``(program_type, branch)`` through the archive's term search index.

        The index is only trusted when the archive's current term is the term
        OBS is serving right now. Every match is still confirmed against the
        live schedule by the caller.
        """
        from .archive_client import ItuArchiveError

        hint = "department_code (örn. 'BLG') vererek yeniden deneyin."
        try:
            index = self.archive.get_index()
            slug = index.get("currentSlug")
            rows = self.archive.get_term_search(str(slug)) if slug else None
        except ItuArchiveError as exc:
            raise ObsError(f"CRN'den branş bulunamadı, ders arşivi okunamadı: {exc}. {hint}") from exc
        if not rows:
            raise ObsError(f"Ders arşivinde güncel dönemin arama dizini yok. {hint}")
        archive_term = index.get("currentTerm")
        active = self._seat_watch_active_term(program_type or "LS")
        if not active or term_key(active) != term_key(archive_term):
            raise ObsError(
                f"Ders arşivinin güncel dönemi ({archive_term!r}) OBS'nin aktif dönemiyle "
                f"({active!r}) eşleşmiyor; CRN'ler dönemler arasında yeniden kullanıldığı için "
                f"arşivden branş çıkarılmadı. {hint}"
            )
        by_crn = {
            str(row[0]): row for row in rows if isinstance(row, list) and len(row) >= 4
        }
        groups: dict[tuple[str, str], list[str]] = {}
        missing: list[dict[str, Any]] = []
        for crn in crns:
            row = by_crn.get(crn)
            branch = str(row[3]).strip().upper() if row and row[3] else ""
            if not branch:
                missing.append({
                    "crn": crn,
                    "reason": "not_in_archive_index",
                    "note": f"CRN arşivin {slug} dizininde yok. {hint}",
                })
                continue
            level = _ARCHIVE_LEVELS.get(str(row[8]).strip().upper()) if len(row) > 8 and row[8] else None
            groups.setdefault((program_type or level or "LS", branch), []).append(crn)
        return groups, missing

    def watch_course_sections(
        self,
        crns: list[str | int],
        department_code: str | None = None,
        program_type: str | None = None,
    ) -> dict[str, Any]:
        """Add sections to the local seat watch list by CRN."""
        from .registration_draft import normalize_crns

        selected = normalize_crns(_crn_strings(crns))
        level = ObsPublicClient._normalize_program_type(program_type) if program_type else None
        branch = (department_code or "").strip().upper()
        if department_code is not None and not branch:
            raise ObsError("department_code boş olamaz.")

        with _STATE_LOCK:
            state, warning = load_seat_watch_state(self.seat_watch_state_path)
            watches = state["watches"]
            if len(set(watches) | set(selected)) > MAX_WATCHED_SECTIONS:
                raise ObsError(
                    f"En fazla {MAX_WATCHED_SECTIONS} şube izlenebilir (şu an {len(watches)}). "
                    "Her kontrol branş başına bir OBS isteği gönderdiği için liste sınırlı. "
                    "unwatch_course_sections ile yer açın."
                )
            if branch:
                groups = {(level or "LS", branch): selected}
                not_found: list[dict[str, Any]] = []
                resolved_by = "department_code"
            else:
                groups, not_found = self._seat_watch_discover(selected, level)
                resolved_by = "archive_search_index"

            added: list[dict[str, Any]] = []
            already: list[dict[str, Any]] = []
            replaced: list[str] = []
            for (group_level, group_branch), group_crns in groups.items():
                schedule = self.obs_public.get_course_schedule(group_level, group_branch)
                listed_branch = str(schedule.get("department_code") or group_branch)
                if normalize_lookup_text(listed_branch) != normalize_lookup_text(group_branch):
                    raise ObsError(
                        f"Bölüm kodu {group_branch!r} OBS'de {listed_branch!r} olarak çözüldü; "
                        "tam bölüm kodunu verin."
                    )
                term = schedule.get("semester")
                if not _usable_term(term):
                    raise ObsError(
                        "OBS aktif dönemi bildirmedi; CRN'ler dönemler arasında yeniden "
                        "kullanıldığı için dönem bilinmeden izleme eklenmedi. Biraz sonra yeniden deneyin."
                    )
                courses = schedule.get("courses") or []
                populated = enrollment_populated(courses)
                by_crn = {str(course.get("crn")): course for course in courses}
                for crn in group_crns:
                    course = by_crn.get(crn)
                    if course is None:
                        not_found.append({
                            "crn": crn,
                            "reason": "not_in_schedule",
                            "note": (
                                f"{listed_branch} ({group_level}) programında bu CRN yok; "
                                "bölüm kodunu ve program tipini kontrol edin."
                            ),
                        })
                        continue
                    existing = watches.get(crn)
                    if existing and term_key(existing.get("term")) == term_key(term):
                        already.append(_entry_summary(existing))
                        continue
                    if existing:
                        replaced.append(crn)
                    entry = {
                        "crn": crn,
                        "program_type": group_level,
                        "department_code": listed_branch,
                        "term": term,
                        **_course_fields(course),
                        "added_at": _now_iso(),
                        "last_observation": None,
                        "last_known": None,
                        "last_change": None,
                    }
                    watches[crn] = entry
                    added.append({
                        **_entry_summary(entry),
                        "current": observe_section(course, populated=populated),
                    })
            if added:
                save_seat_watch_state(self.seat_watch_state_path, state)
            result: dict[str, Any] = {
                "added": added,
                "already_watched": already,
                "not_found": not_found,
                "resolved_by": resolved_by,
                "watch_count": len(watches),
                "watch_limit": MAX_WATCHED_SECTIONS,
                "baseline_note": (
                    "'current' yalnızca bilgi içindir. Karşılaştırma tabanı ilk "
                    "check_watched_course_sections çağrısında kurulur."
                ),
                "background_note": NO_BACKGROUND_NOTE,
            }
            if replaced:
                result["replaced_stale"] = replaced
                result["replaced_note"] = (
                    "Bu CRN'ler önceki bir dönemden izleniyordu; yeni dönemin şubesiyle değiştirildi."
                )
            if warning:
                result["state_warning"] = warning
            return result

    def list_watched_course_sections(self) -> dict[str, Any]:
        """List the watched sections with their last stored observation. Reads no network."""
        with _STATE_LOCK:
            state, warning = load_seat_watch_state(self.seat_watch_state_path)
        sections = [
            {
                **_entry_summary(entry),
                "last_observation": entry.get("last_observation"),
                "last_change": entry.get("last_change"),
            }
            for entry in sorted(
                state["watches"].values(),
                key=lambda item: (str(item.get("course_code") or ""), item["crn"]),
            )
        ]
        result: dict[str, Any] = {
            "watch_count": len(sections),
            "watch_limit": MAX_WATCHED_SECTIONS,
            "terms": sorted({str(item["term"]) for item in sections if item.get("term")}),
            "last_check_at": state.get("last_check_at"),
            "sections": sections,
            "source_note": (
                "Bu liste yerel kayıttır; OBS okunmadı. Güncel kontenjan için "
                "check_watched_course_sections çağırın."
            ),
            "background_note": NO_BACKGROUND_NOTE,
        }
        if warning:
            result["state_warning"] = warning
        return result

    def unwatch_course_sections(
        self,
        crns: list[str | int] | None = None,
        stale_only: bool = False,
        clear_all: bool = False,
    ) -> dict[str, Any]:
        """Remove watched sections: named CRNs, entries from a past term, or everything."""
        from .registration_draft import normalize_crns

        if sum((crns is not None, bool(stale_only), bool(clear_all))) != 1:
            raise ObsError("Tam olarak biri verilmeli: crns, stale_only=true veya clear_all=true.")
        selected = normalize_crns(_crn_strings(crns)) if crns is not None else []

        with _STATE_LOCK:
            state, warning = load_seat_watch_state(self.seat_watch_state_path)
            watches = state["watches"]
            not_watched: list[str] = []
            if clear_all:
                targets = list(watches)
            elif stale_only:
                targets = []
                terms: dict[str, str | None] = {}
                for crn, entry in watches.items():
                    level = str(entry.get("program_type") or "LS")
                    if level not in terms:
                        terms[level] = self._seat_watch_active_term(level)
                    if terms[level] is None:
                        raise ObsError(
                            "OBS aktif dönemi okunamadı; hangi kayıtların eski döneme ait "
                            "olduğu belirlenemediği için hiçbir şey silinmedi."
                        )
                    if term_key(entry.get("term")) != term_key(terms[level]):
                        targets.append(crn)
            else:
                targets = [crn for crn in selected if crn in watches]
                not_watched = [crn for crn in selected if crn not in watches]
            removed = [_entry_summary(watches.pop(crn)) for crn in targets]
            if removed:
                save_seat_watch_state(self.seat_watch_state_path, state)
            result: dict[str, Any] = {
                "removed": removed,
                "not_watched": not_watched,
                "watch_count": len(watches),
                "watch_limit": MAX_WATCHED_SECTIONS,
            }
            if warning:
                result["state_warning"] = warning
            return result

    def check_watched_course_sections(self) -> dict[str, Any]:
        """Read the public schedule for the watched branches and report seat changes."""
        with _STATE_LOCK:
            started = datetime.now(tz=UTC)
            checked_at = started.isoformat()
            state, warning = load_seat_watch_state(self.seat_watch_state_path)
            watches = state["watches"]
            ttl = getattr(self.obs_public, "_schedule_cache_ttl", None) if watches else None
            ttl = float(ttl) if isinstance(ttl, (int, float)) else None

            results: dict[str, dict[str, Any]] = {}
            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            active_terms: dict[str, str | None] = {}
            for crn, entry in watches.items():
                level = str(entry.get("program_type") or "LS")
                if level not in active_terms:
                    active_terms[level] = self._seat_watch_active_term(level)
                active = active_terms[level]
                if active and term_key(active) != term_key(entry.get("term")):
                    # A past-term entry costs no schedule request at all.
                    results[crn] = _stale_result(entry, active)
                    continue
                groups.setdefault((level, str(entry.get("department_code") or "")), []).append(entry)

            sources: list[dict[str, Any]] = []
            dirty = False
            for (level, branch), entries in groups.items():
                source: dict[str, Any] = {"program_type": level, "department_code": branch}
                sources.append(source)
                try:
                    schedule = self.obs_public.get_course_schedule(level, branch)
                except ObsError as exc:
                    source.update({"status": "fetch_failed", "error": str(exc)})
                    for entry in entries:
                        results[entry["crn"]] = _skipped_result(
                            entry, "fetch_failed", "Program okunamadı; önceki gözlem korundu."
                        )
                    continue
                term = schedule.get("semester")
                fetched = _parse_iso(schedule.get("fetched_at"))
                fetched_at = fetched.isoformat() if fetched else None
                age = max(0.0, (started - fetched).total_seconds()) if fetched else None
                source.update({
                    "status": "ok",
                    "term": term,
                    "section_count": len(schedule.get("courses") or []),
                    "fetched_at": fetched_at,
                    # Stamped before this call started means the client served its cache.
                    "from_cache": fetched < started if fetched else None,
                    "age_seconds": round(age) if age is not None else None,
                })
                if not schedule_usable(schedule):
                    source["status"] = "schedule_unavailable"
                    source["reason"] = schedule.get("parse_warning") or schedule.get("message")
                    for entry in entries:
                        results[entry["crn"]] = _skipped_result(
                            entry, "schedule_unavailable",
                            "OBS bu bölüm için boş veya okunamayan bir program döndürdü; "
                            "bu, şubenin kaldırıldığı anlamına gelmez.",
                        )
                    continue
                courses = schedule["courses"]
                populated = enrollment_populated(courses)
                source["enrollment_populated"] = populated
                by_crn = {str(course.get("crn")): course for course in courses}
                for entry in entries:
                    crn = entry["crn"]
                    if not _usable_term(term):
                        results[crn] = _skipped_result(
                            entry, "term_unverified",
                            "OBS aktif dönemi bildirmedi; CRN başka bir döneme ait olabileceği "
                            "için karşılaştırma yapılmadı.",
                        )
                        continue
                    if term_key(term) != term_key(entry.get("term")):
                        results[crn] = _stale_result(entry, str(term))
                        continue
                    course = by_crn.get(crn)
                    observation = observe_section(course, populated=populated)
                    observation["observed_at"] = fetched_at or checked_at
                    observation["source_fetched_at"] = fetched_at
                    previous = entry.get("last_observation")
                    # The same OBS read as the last check: nothing new to compare.
                    repeat = bool(
                        fetched_at and previous
                        and previous.get("source_fetched_at") == fetched_at
                    )
                    changes: list[str] = []
                    compared = None
                    if not repeat:
                        last_known = entry.get("last_known")
                        changes = diff_observation(previous, last_known, observation)
                        compared = _figures(
                            last_known if SEAT_CHANGES & set(changes) else previous
                        )
                        entry["last_observation"] = observation
                        if observation["status"] in KNOWN_STATUSES:
                            entry["last_known"] = observation
                        if changes and changes != ["baseline"]:
                            entry["last_change"] = {
                                "changes": changes,
                                "at": observation["observed_at"],
                                "from": compared,
                                "to": _figures(observation),
                            }
                        if course is not None:
                            entry.update(_course_fields(course))
                        dirty = True
                    item: dict[str, Any] = {
                        **_entry_summary(entry),
                        **{k: v for k, v in observation.items() if k != "source_fetched_at"},
                        "changes": changes,
                        "compared_with": compared,
                        "repeat_of_last_check": repeat,
                        "last_change": entry.get("last_change"),
                    }
                    if course is not None and course.get("reservation") not in (None, "", "-"):
                        item["reservation"] = course.get("reservation")
                        item["reservation_note"] = (
                            "Kontenjanın bir kısmı belirli programlara ayrılmış olabilir; boş yer "
                            "her öğrenci için alınabilir olmayabilir."
                        )
                    note = _observation_note(observation, changes, repeat)
                    if note:
                        item["note"] = note
                    results[crn] = item

            if dirty:
                state["last_check_at"] = checked_at
                save_seat_watch_state(self.seat_watch_state_path, state)

            ordered = [results[crn] for crn in watches if crn in results]

            def crns_with(change: str) -> list[str]:
                return [item["crn"] for item in ordered if change in item["changes"]]

            def crns_in(*statuses: str) -> list[str]:
                return [item["crn"] for item in ordered if item["status"] in statuses]

            ok_sources = [source for source in sources if source["status"] == "ok"]
            all_cached = bool(ok_sources) and all(source["from_cache"] for source in ok_sources)
            ages = [source["age_seconds"] for source in ok_sources if source["from_cache"]]
            result: dict[str, Any] = {
                "checked_at": checked_at,
                "watch_count": len(watches),
                "watch_limit": MAX_WATCHED_SECTIONS,
                "branches_read": len(sources),
                "summary": {
                    "opened": crns_with("opened"),
                    "filled": crns_with("filled"),
                    "capacity_raised": crns_with("capacity_raised"),
                    "delisted": crns_with("delisted"),
                    "baseline_established": crns_with("baseline"),
                    "open_now": crns_in("open"),
                    "unknown": crns_in("unknown"),
                    "not_listed": crns_in("not_listed"),
                    "stale_term": crns_in("stale_term"),
                    "not_checked": crns_in("fetch_failed", "schedule_unavailable", "term_unverified"),
                },
                "sections": ordered,
                "sources": sources,
                "freshness": {
                    "all_from_cache": all_cached,
                    "oldest_data_age_seconds": max(ages) if ages else None,
                    "cache_ttl_seconds": ttl,
                },
                "background_note": NO_BACKGROUND_NOTE,
            }
            if all_cached:
                wait = max(0, round(ttl - min(ages))) if ttl is not None else None
                result["freshness"]["retry_after_seconds"] = wait
                result["freshness"]["note"] = (
                    f"Bu sonuç OBS'den yeniden okunmadı; önbellekteki veri {max(ages)} saniyelik. "
                    + (f"Yaklaşık {wait} saniye geçmeden yeniden çağırmak aynı sayıları döndürür. "
                       if wait is not None else "")
                    + "Aracı döngüde art arda çağırmayın."
                )
            if not watches:
                result["note"] = "İzlenen şube yok. watch_course_sections ile CRN ekleyin."
            if result["summary"]["stale_term"]:
                result["stale_note"] = (
                    "Bazı kayıtlar önceki bir döneme ait. CRN'ler dönemler arasında yeniden "
                    "kullanıldığı için bunlar karşılaştırılmadı. "
                    "unwatch_course_sections(stale_only=true) ile temizleyin."
                )
            if warning:
                result["state_warning"] = warning
            return result


SEAT_WATCH_TOOLS: list[dict[str, Any]] = [
    {
        "name": "watch_course_sections",
        "title": "Watch Course Sections",
        "description": (
            "Add course sections to a local seat watch list by CRN, so a later "
            "check_watched_course_sections call can report when a full section opens. "
            "Needs no login. With 'department_code' the CRNs are looked up in that "
            "department's public OBS schedule. Without it the branch is found from the "
            "course archive's current-term search index, then confirmed against the live "
            "schedule; this only works while the archive's current term is the term OBS is "
            f"serving. At most {MAX_WATCHED_SECTIONS} sections can be watched. The entry "
            "stores the term, because CRNs are reused across terms. This does not start any "
            "monitoring: the server cannot run in the background and never notifies anyone. "
            "Seats are read only when check_watched_course_sections is called."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "crns": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^[0-9]{4,5}$"},
                    "minItems": 1,
                    "maxItems": 12,
                    "uniqueItems": True,
                    "description": "CRNs to watch, e.g. ['12469'].",
                },
                "department_code": {
                    "type": "string",
                    "description": "Course branch code, e.g. 'BLG'. Omit to discover it from the CRN.",
                },
                "program_type": {
                    "type": "string",
                    "description": "Program level: LS (default), LU, ÖL or LUİ.",
                },
            },
            "required": ["crns"],
            "additionalProperties": False,
        },
    },
    {
        "name": "unwatch_course_sections",
        "title": "Unwatch Course Sections",
        "description": (
            "Remove sections from the local seat watch list. Give exactly one of: 'crns' "
            "(those sections), 'stale_only' (every entry from a term other than the one OBS "
            "is serving now), or 'clear_all' (the whole list). Only the local list changes; "
            "nothing is sent to OBS apart from reading the active term for 'stale_only'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "crns": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^[0-9]{4,5}$"},
                    "minItems": 1,
                    "maxItems": 12,
                    "uniqueItems": True,
                },
                "stale_only": {"type": "boolean", "default": False},
                "clear_all": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_watched_course_sections",
        "title": "List Watched Course Sections",
        "description": (
            "List the sections on the local seat watch list with the term each was added in "
            "and the last stored observation. Reads only local state, not OBS, so the "
            "numbers are as old as the last check_watched_course_sections call."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "check_watched_course_sections",
        "title": "Check Watched Course Sections",
        "description": (
            "Read the public OBS schedule now for the watched sections (one request per "
            "branch, no login) and report per section: capacity, enrolled, seats free, and "
            "what changed since the previous check ('opened', 'filled', 'capacity_raised', "
            "'delisted', ...). The first check of a section only sets a baseline. Status "
            "'unknown' means OBS gave no usable numbers (missing values, or the enrolled "
            "column not populated yet) and is never a free seat; 'not_listed' means the CRN "
            "is absent from the schedule, which is not the same as full. Entries from a past "
            "term are reported as 'stale_term' and not compared. This is a one-shot check: "
            "the server cannot run in the background and sends no notifications, so a "
            "periodic check must be driven by the user or by a scheduled task on the client. "
            "Schedule reads are cached for a short time; when 'freshness.all_from_cache' is "
            "true the numbers were not re-read, so wait 'retry_after_seconds' instead of "
            "calling again in a loop."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

# Tools that write the local watch file. list_watched_course_sections only reads it.
SEAT_WATCH_STATEFUL_TOOL_NAMES = frozenset({
    "watch_course_sections",
    "unwatch_course_sections",
    "check_watched_course_sections",
})
