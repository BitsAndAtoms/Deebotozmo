from typing import Set

from  commands import WaterLevel

from .helpers import verify_DisplayNameEnum_unique


def test_WaterLevel_unique():
    verify_DisplayNameEnum_unique(WaterLevel)

