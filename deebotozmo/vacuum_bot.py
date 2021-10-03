"""Vacuum bot module."""
import asyncio
import inspect
import logging
import re
from typing import Any, Dict, Final, Optional, Union

import aiohttp

from deebotozmo.commands import (
    COMMANDS,
    Clean,
    Command,
    GetBattery,
    GetChargeState,
    GetCleanInfo,
    GetCleanLogs,
    GetError,
    GetFanSpeed,
    GetLifeSpan,
    GetStats,
    GetWaterInfo,
)
from deebotozmo.commands.clean import CleanAction
from deebotozmo.commands_old import Command as OldCommand
from deebotozmo.ecovacs_api import EcovacsAPI
from deebotozmo.ecovacs_json import EcovacsJSON
from deebotozmo.event_emitter import EventEmitter, PollingEventEmitter, VacuumEmitter
from deebotozmo.events import (
    BatteryEvent,
    CleanLogEvent,
    ErrorEvent,
    FanSpeedEvent,
    LifeSpanEvent,
    StatsEvent,
    StatusEvent,
    WaterInfoEvent,
)
from deebotozmo.map import Map
from deebotozmo.models import RequestAuth, Vacuum, VacuumState
from deebotozmo.util import get_refresh_function

_LOGGER = logging.getLogger(__name__)

_COMMAND_REPLACE_PATTERN = "^((on)|(off)|(report))"
_COMMAND_REPLACE_REPLACEMENT = "get"


class VacuumBot:
    """Vacuum bot representation."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: RequestAuth,
        vacuum: Vacuum,
        *,
        continent: str,
        country: str,
        verify_ssl: Union[bool, str] = True,
    ):
        self._semaphore = asyncio.Semaphore(3)
        self._session = session
        self._status: StatusEvent = StatusEvent(vacuum.status == 1, None)
        self.vacuum: Final[Vacuum] = vacuum

        portal_url = EcovacsAPI.PORTAL_URL_FORMAT.format(continent=continent)

        if country.lower() == "cn":
            portal_url = EcovacsAPI.PORTAL_URL_FORMAT_CN

        self.json: EcovacsJSON = EcovacsJSON(session, auth, portal_url, verify_ssl)

        self.fw_version: Optional[str] = None

        self.map: Final = Map(self.execute_command)

        status_ = EventEmitter[StatusEvent](
            get_refresh_function(
                [GetChargeState(), GetCleanInfo()],
                self.execute_command,
            )
        )
        self.events: Final = VacuumEmitter(
            battery=EventEmitter[BatteryEvent](
                get_refresh_function([GetBattery()], self.execute_command)
            ),
            clean_logs=EventEmitter[CleanLogEvent](
                get_refresh_function([GetCleanLogs()], self.execute_command)
            ),
            error=EventEmitter[ErrorEvent](
                get_refresh_function([GetError()], self.execute_command)
            ),
            fan_speed=EventEmitter[FanSpeedEvent](
                get_refresh_function([GetFanSpeed()], self.execute_command)
            ),
            lifespan=PollingEventEmitter[LifeSpanEvent](
                60, get_refresh_function([GetLifeSpan()], self.execute_command), status_
            ),
            map=self.map.events.map,
            rooms=self.map.events.rooms,
            stats=EventEmitter[StatsEvent](
                get_refresh_function([GetStats()], self.execute_command)
            ),
            status=status_,
            water_info=EventEmitter[WaterInfoEvent](
                get_refresh_function([GetWaterInfo()], self.execute_command)
            ),
        )

        async def on_status(event: StatusEvent) -> None:
            last_status = self._status
            self._status = event
            if (not last_status.available) and event.available:
                # bot was unavailable
                for name, obj in inspect.getmembers(
                    self.events, lambda obj: isinstance(obj, EventEmitter)
                ):
                    if name != "status":
                        obj.request_refresh()
            elif (
                last_status.state != VacuumState.DOCKED
                and event.state == VacuumState.DOCKED
            ):
                self.events.clean_logs.request_refresh()

        self.events.status.subscribe(on_status)

    async def execute_command(self, command: Union[Command, OldCommand]) -> None:
        """Execute given command and handle response."""
        if (
            command == Clean(CleanAction.RESUME)
            and self._status.state != VacuumState.PAUSED
        ):
            command = Clean(CleanAction.START)
        elif (
            command == Clean(CleanAction.START)
            and self._status.state == VacuumState.PAUSED
        ):
            command = Clean(CleanAction.RESUME)

        async with self._semaphore:
            response = await self.json.send_command(command, self.vacuum)

        await self.handle(command.name, response, command)

    def set_available(self, available: bool) -> None:
        """Set available."""
        status = StatusEvent(available, self._status.state)
        self.events.status.notify(status)

    def _set_state(self, state: VacuumState) -> None:
        self.events.status.notify(StatusEvent(True, state))

    # ---------------------------- EVENT HANDLING ----------------------------

    async def handle(
        self,
        command_name: str,
        data: Dict[str, Any],
        requested_command: Optional[Union[Command, OldCommand]],
    ) -> None:
        """Handle the given event.

        :param command_name: the name of the event or request
        :param data: the data of it
        :param requested_command: The request command object. None -> MQTT
        :return: None
        """
        _LOGGER.debug("Handle %s: %s", command_name, data)

        if requested_command and isinstance(requested_command, Command):
            requested_command.handle_requested(self.events, data)
        else:
            # Handle command start start with "on","off","report" the same as "get" commands
            command_name = re.sub(
                _COMMAND_REPLACE_PATTERN, _COMMAND_REPLACE_REPLACEMENT, command_name
            )

            command = COMMANDS.get(command_name, None)
            if command:
                command.handle(self.events, data)
            else:
                await self._handle_old(
                    command_name, data, requested_command is not None
                )

    async def _handle_old(
        self, event_name: str, event: dict, requested: bool = True
    ) -> None:
        # pylint: disable=too-many-branches

        event_name = event_name.lower()

        prefixes = [
            "on",  # incoming events (on)
            "off",  # incoming events for (3rd) unknown/unsaved map
            "report",  # incoming events (report)
            "get",  # remove from "get" commands
        ]

        for prefix in prefixes:
            if event_name.startswith(prefix):
                event_name = event_name[len(prefix) :]

        # OZMO T8 series and newer
        if event_name.endswith("_V2".lower()):
            event_name = event_name[:-3]

        if event_name in [
            "speed",
            "waterinfo",
            "lifespan",
            "stats",
            "battery",
            "chargestate",
            "charge",
            "clean",
            "cleanlogs",
            "error",
            "playsound",
            "cleaninfo",
        ]:
            raise RuntimeError(
                "Commands support new format. Should never happen! Please contact developers."
            )

        if requested:
            if event.get("ret") == "ok":
                event = event.get("resp", event)
            else:
                _LOGGER.warning('Event %s where ret != "ok": %s', event_name, event)
                return

        event_body = event.get("body", {})
        event_header = event.get("header", {})

        if not (event_body and event_header):
            _LOGGER.warning("Invalid Event %s: %s", event_name, event)
            return

        event_data = event_body.get("data", {})

        fw_version = event_header.get("fwVer")
        if fw_version:
            self.fw_version = fw_version

        if "map" in event_name or event_name == "pos":
            await self.map.handle(event_name, event_data, requested)
        elif event_name.startswith("set"):
            # ignore set commands for now
            pass
        else:
            _LOGGER.debug("Unknown event: %s with %s", event_name, event)
