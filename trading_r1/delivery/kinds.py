# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The kinds of channel a report can go to. Adding one is a module with a `KIND` and an entry
here; the command line, the forms and the API follow from its declaration."""

from __future__ import annotations

from trading_r1.delivery import email, folder, telegram
from trading_r1.delivery.base import Kind

KINDS: dict[str, Kind] = {k.name: k for k in (telegram.KIND, folder.KIND, email.KIND)}

__all__ = ["KINDS"]
