"""Charge state commands."""
import logging
from typing import Any, Dict, Optional

from events import StatusEvent
from models import VacuumState
from .base import _CODE, VacuumEmitter, _NoArgsCommand

_LOGGER = logging.getLogger(__name__)


class GetChargeState(_NoArgsCommand):
    """Get charge state command."""

    name = "getChargeState"

    @classmethod
    def _handle_body_data_dict(
        cls, events: VacuumEmitter, data: Dict[str, Any]
    ) -> bool:
        """Handle message->body->data and notify the correct event subscribers.

        :return: True if data was valid and no error was included
        """
        if data.get("isCharging") == 1:
            events.status.notify(StatusEvent(True, VacuumState.DOCKED))
        return True

    @classmethod
    def _handle_body(cls, events: VacuumEmitter, body: Dict[str, Any]) -> bool:
        if _CODE not in body or body[_CODE] == 0:
            return cls._handle_body_data_dict(events, body.get("data", body))

        status: Optional[VacuumState] = None
        if body.get("msg", None) == "fail":
            if body["code"] == "30007":  # Already charging
                status = VacuumState.DOCKED
            elif body["code"] == "5":  # Busy with another command
                status = VacuumState.ERROR
            elif body["code"] == "3":  # Bot in stuck state, example dust bin out
                status = VacuumState.ERROR

        if status:
            events.status.notify(StatusEvent(True, VacuumState.DOCKED))
            return True

        return False
