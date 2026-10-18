"""Repair issues: tell the user when Heat Planner is running on guesswork.

The coordinator calls :func:`async_check_issues` after every refresh. Issues
are raised only after a grace period (prices and meters drop out briefly all
the time) and are deleted again as soon as the cause is gone. Each problem is
also logged once when it starts and once when it is over.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import (
    CONF_USE_HISTORY,
    DOMAIN,
    HOUSE_METER_UNAVAILABLE_AFTER,
    ISSUE_HOUSE_METER_UNAVAILABLE,
    ISSUE_PRICE_UNAVAILABLE,
    ISSUE_RECORDER_MISSING,
    PRICE_UNAVAILABLE_AFTER,
)
from .energy import house_meter, tariff_from_options
from .tariff import MODE_FIXED

if TYPE_CHECKING:
    from .coordinator import HeatPlannerCoordinator

_LOGGER = logging.getLogger(__name__)

ALL_ISSUES = (ISSUE_PRICE_UNAVAILABLE, ISSUE_HOUSE_METER_UNAVAILABLE, ISSUE_RECORDER_MISSING)

# entry_id -> issue kind -> when the problem was first seen
_SINCE: dict[str, dict[str, datetime]] = {}


def issue_id(kind: str, entry_id: str) -> str:
    """Issue ids are per config entry so several homes do not share one."""
    return f"{kind}_{entry_id}"


def price_missing(coordinator: HeatPlannerCoordinator, now: datetime) -> bool:
    """No spot price for the current hour (a fixed-price contract needs none)."""
    if tariff_from_options(coordinator.options).mode == MODE_FIXED:
        return False
    hour = now.replace(minute=0, second=0, microsecond=0)
    return hour not in coordinator.spot_hours


def meter_missing(coordinator: HeatPlannerCoordinator) -> str | None:
    """The configured whole-house meter when it is unavailable, else None."""
    meter = house_meter(coordinator.options)
    if not meter:
        return None
    state = coordinator.hass.states.get(meter)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return meter
    return None


def recorder_missing(hass: HomeAssistant, options: dict[str, object]) -> bool:
    """History learning was chosen but the recorder is not running."""
    return bool(options.get(CONF_USE_HISTORY)) and "recorder" not in hass.config.components


def _track(
    coordinator: HeatPlannerCoordinator,
    kind: str,
    active: bool,
    now: datetime,
    grace: timedelta,
    placeholders: dict[str, str],
    *,
    learn_more_url: str | None = None,
) -> None:
    entry = coordinator.config_entry
    since = _SINCE.setdefault(entry.entry_id, {})
    iid = issue_id(kind, entry.entry_id)
    if not active:
        if since.pop(kind, None) is not None:
            _LOGGER.info("%s: %s is resolved", entry.title, kind.replace("_", " "))
            ir.async_delete_issue(coordinator.hass, DOMAIN, iid)
        return
    if kind not in since:
        since[kind] = now
        _LOGGER.warning("%s: %s %s", entry.title, kind.replace("_", " "), placeholders or "")
    if now - since[kind] >= grace:
        ir.async_create_issue(
            coordinator.hass,
            DOMAIN,
            iid,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=kind,
            translation_placeholders={"title": entry.title, **placeholders},
            learn_more_url=learn_more_url,
        )


@callback
def async_check_issues(coordinator: HeatPlannerCoordinator) -> None:
    """Create or delete this entry's repair issues."""
    now = dt_util.now()
    _track(
        coordinator,
        ISSUE_PRICE_UNAVAILABLE,
        price_missing(coordinator, now),
        now,
        PRICE_UNAVAILABLE_AFTER,
        {"source": coordinator.prices.description},
    )
    meter = meter_missing(coordinator)
    _track(
        coordinator,
        ISSUE_HOUSE_METER_UNAVAILABLE,
        meter is not None,
        now,
        HOUSE_METER_UNAVAILABLE_AFTER,
        {"entity": meter or ""},
    )
    _track(
        coordinator,
        ISSUE_RECORDER_MISSING,
        recorder_missing(coordinator.hass, coordinator.options),
        now,
        timedelta(0),
        {},
    )


@callback
def async_clear_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Forget the entry's issues (unload or removal)."""
    _SINCE.pop(entry_id, None)
    for kind in ALL_ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(kind, entry_id))
