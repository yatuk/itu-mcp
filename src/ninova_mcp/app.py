"""``NinovaMcpApp``: the object whose methods are the MCP tools.

The class has no body of its own. Shared state and the lazy clients come from
``AppBase``, and each domain contributes its tool methods through a mixin.
A tool is found by name with ``getattr(app, name)``, so a tool method name
must be defined by exactly one mixin.
"""

from __future__ import annotations

from .app_archive import ArchiveHistoryToolsMixin
from .app_base import AppBase
from .app_campus import CampusToolsMixin
from .app_core import SessionToolsMixin
from .app_export import TableExportToolsMixin
from .app_files import PageFileToolsMixin
from .app_gpa_simulation import GpaSimulationToolsMixin
from .app_library import LibraryToolsMixin
from .app_mail import MailToolsMixin
from .app_ninova import NinovaCourseToolsMixin
from .app_obs import ObsRecordToolsMixin
from .app_planning import PlanningToolsMixin
from .app_portal import PortalToolsMixin
from .app_public import PublicCatalogToolsMixin
from .app_registration import RegistrationToolsMixin
from .app_search import SearchToolsMixin
from .app_tracking import TrackingToolsMixin
from .archive_tools import ArchiveDatasetToolsMixin
from .calendar_export import CalendarExportToolsMixin
from .moodle_tools import MoodleToolsMixin
from .seat_watch import SeatWatchToolsMixin


# The three feature mixins stay last: they were the bases of the class when
# it still had a body, so anything defined in the mixins above wins over them,
# as the body did.
class NinovaMcpApp(
    AppBase,
    SessionToolsMixin,
    MailToolsMixin,
    NinovaCourseToolsMixin,
    TrackingToolsMixin,
    PageFileToolsMixin,
    ObsRecordToolsMixin,
    RegistrationToolsMixin,
    PortalToolsMixin,
    PublicCatalogToolsMixin,
    CampusToolsMixin,
    PlanningToolsMixin,
    LibraryToolsMixin,
    MoodleToolsMixin,
    ArchiveHistoryToolsMixin,
    GpaSimulationToolsMixin,
    SearchToolsMixin,
    ArchiveDatasetToolsMixin,
    SeatWatchToolsMixin,
    CalendarExportToolsMixin,
    TableExportToolsMixin,
):
    pass
