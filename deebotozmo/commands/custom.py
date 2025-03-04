"""Custom command module."""
from typing import Any, Dict, List, Union

from  event_emitter import _LOGGER, VacuumEmitter
from  events import CustomCommandEvent


class CustomCommand:
    """Custom command, used when user wants to execute a command, which is not part of this library."""

    def __init__(self, name: str, args: Union[Dict, List, None] = None) -> None:
        self._name = name
        if args is None:
            args = {}
        self._args = args

    @property
    def name(self) -> str:
        """Command name."""
        return self._name

    @property
    def args(self) -> Union[Dict[str, Any], List]:
        """Command additional arguments."""
        return self._args

    def handle_requested(self, events: VacuumEmitter, response: Dict[str, Any]) -> bool:
        """Handle response from a manual requested command.

        :return: True if data was valid and no error was included
        """
        if response.get("ret") == "ok":
            data = response.get("resp", response)
            events.custom_command.notify(CustomCommandEvent(self.name, data))
            return True

        _LOGGER.warning('Command "%s" was not successfully: %s', self.name, response)
        return False
