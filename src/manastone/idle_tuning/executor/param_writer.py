"""ParamWriter — ABC and implementations for writing PID params to the robot."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

from manastone.common.models import PIDParams


class ParamWriter(ABC):
    @abstractmethod
    async def write_chain_params(
        self, chain_name: str, params: Dict[str, PIDParams]
    ) -> None: ...

    @abstractmethod
    async def rollback_chain(
        self, chain_name: str, prev_params: Optional[Dict[str, PIDParams]]
    ) -> None: ...

    @abstractmethod
    def get_current_params(self, joint_name: str) -> Optional[PIDParams]: ...


class MockParamWriter(ParamWriter):
    def __init__(self):
        self._params: Dict[str, PIDParams] = {}
        self._prev_params: Dict[str, PIDParams] = {}

    async def write_chain_params(
        self, chain_name: str, params: Dict[str, PIDParams]
    ) -> None:
        self._prev_params.update(self._params)
        self._params.update(params)

    async def rollback_chain(
        self, chain_name: str, prev_params: Optional[Dict[str, PIDParams]]
    ) -> None:
        if prev_params:
            self._params.update(prev_params)

    def get_current_params(self, joint_name: str) -> Optional[PIDParams]:
        return self._params.get(joint_name)


class RealParamWriter(ParamWriter):
    """Stub — uses rosbridge in Phase 4."""

    def __init__(self, rosbridge_url: str):
        self.url = rosbridge_url

    async def write_chain_params(
        self, chain_name: str, params: Dict[str, PIDParams]
    ) -> None:
        raise NotImplementedError("RealParamWriter requires rosbridge — Phase 4")

    async def rollback_chain(
        self, chain_name: str, prev_params: Optional[Dict[str, PIDParams]] = None
    ) -> None:
        raise NotImplementedError("RealParamWriter requires rosbridge — Phase 4")

    def get_current_params(self, joint_name: str) -> Optional[PIDParams]:
        return None
