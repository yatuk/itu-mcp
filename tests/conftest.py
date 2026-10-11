"""Shared pytest setup.

Some tests build a client without passing credentials. They used to pass only
because a developer ``.env`` in a parent directory was picked up; parent
directories are no longer searched, so placeholder values are supplied here.
CI and the unittest runner already pass the same placeholders.
"""

from __future__ import annotations

import os

os.environ.setdefault("NINOVA_USERNAME", "dummy")
os.environ.setdefault("NINOVA_PASSWORD", "dummy")
