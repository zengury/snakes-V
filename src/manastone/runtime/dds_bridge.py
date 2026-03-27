"""
DDSBridge — rosbridge WebSocket subscriber (ABC + Real + Mock).

Real mode: connects to rosbridge_suite at ROSBRIDGE_URL (ws://localhost:9090).
Mock mode: MockDDSBridge emits simulated 50Hz joint data via MockJointSimulator.

Reconnect policy: 5s backoff, max 3 retries, then raises DDSConnectionLostError.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from manastone.common.config import ManaConfig


class DDSConnectionLostError(Exception):
    """Raised after max reconnect retries are exhausted."""


# rosbridge v2 protocol helpers
def _subscribe_msg(topic: str, msg_type: str, throttle_rate: int = 0) -> str:
    return json.dumps(
        {"op": "subscribe", "topic": topic, "type": msg_type, "throttle_rate": throttle_rate}
    )


def _call_service_msg(service: str, args: Dict[str, Any], call_id: str = "") -> str:
    return json.dumps({"op": "call_service", "service": service, "args": args, "id": call_id})


class DDSBridge(ABC):
    """Abstract DDS bridge. Injected by ManaConfig factory."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def subscribe(
        self,
        topic: str,
        msg_type: str,
        callback: Callable[[Dict[str, Any]], Any],
        throttle_rate: int = 0,
    ) -> None: ...

    @abstractmethod
    async def call_service(self, service: str, args: Dict[str, Any]) -> Dict[str, Any]: ...

    @abstractmethod
    async def disconnect(self) -> None: ...


# ---------------------------------------------------------------------------
# Real bridge
# ---------------------------------------------------------------------------


class RealDDSBridge(DDSBridge):
    """Connects to rosbridge_suite over WebSocket."""

    _RECONNECT_DELAY_S = 5.0
    _MAX_RETRIES = 3

    def __init__(self) -> None:
        self._url = ManaConfig.get().get_rosbridge_url()
        self._ws: Any = None
        self._subscribers: Dict[str, List[Callable]] = {}
        self._service_futures: Dict[str, asyncio.Future] = {}
        self._running = False

    async def connect(self) -> None:
        import websockets  # type: ignore[import]

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                self._ws = await websockets.connect(self._url)
                self._running = True
                asyncio.create_task(self._message_loop())
                return
            except Exception as exc:
                last_exc = exc
                if attempt < self._MAX_RETRIES:
                    await asyncio.sleep(self._RECONNECT_DELAY_S)
        raise DDSConnectionLostError(
            f"Failed to connect to {self._url} after {self._MAX_RETRIES} attempts"
        ) from last_exc

    async def subscribe(
        self,
        topic: str,
        msg_type: str,
        callback: Callable[[Dict[str, Any]], Any],
        throttle_rate: int = 0,
    ) -> None:
        self._subscribers.setdefault(topic, []).append(callback)
        if self._ws:
            await self._ws.send(_subscribe_msg(topic, msg_type, throttle_rate))

    async def call_service(self, service: str, args: Dict[str, Any]) -> Dict[str, Any]:
        call_id = f"{service}-{time.monotonic()}"
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._service_futures[call_id] = fut
        if self._ws:
            await self._ws.send(_call_service_msg(service, args, call_id))
        return await asyncio.wait_for(fut, timeout=10.0)

    async def _message_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                op = msg.get("op", "")
                if op == "publish":
                    topic = msg.get("topic", "")
                    for cb in self._subscribers.get(topic, []):
                        result = cb(msg.get("msg", {}))
                        if asyncio.iscoroutine(result):
                            await result
                elif op == "service_response":
                    call_id = msg.get("id", "")
                    if call_id in self._service_futures:
                        self._service_futures.pop(call_id).set_result(msg.get("values", {}))
        except Exception:
            self._running = False

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None


# ---------------------------------------------------------------------------
# Mock bridge
# ---------------------------------------------------------------------------


class MockJointSimulator:
    """Generates plausible sinusoidal joint sensor data."""

    def __init__(self, joint_name: str) -> None:
        self.joint_name = joint_name
        self._t = 0.0
        self._freq = 0.5 + random.uniform(-0.2, 0.2)
        self._amp = 0.1 + random.uniform(0.0, 0.05)
        self._temp = 25.0 + random.uniform(0.0, 5.0)

    def step(self, dt: float) -> Dict[str, Any]:
        self._t += dt
        position = self._amp * math.sin(2 * math.pi * self._freq * self._t)
        velocity = self._amp * 2 * math.pi * self._freq * math.cos(
            2 * math.pi * self._freq * self._t
        )
        effort = 0.5 * velocity + random.gauss(0, 0.002)
        self._temp += random.gauss(0.0, 0.01)
        return {
            "name": self.joint_name,
            "position": position,
            "velocity": velocity,
            "effort": effort,
            "temperature": self._temp,
        }


class MockDDSBridge(DDSBridge):
    """Emits simulated 50Hz joint data without a real rosbridge connection."""

    _PUBLISH_INTERVAL_S = 0.02  # 50Hz

    def __init__(self) -> None:
        self._simulators: Dict[str, MockJointSimulator] = {}
        self._subscribers: Dict[str, List[Callable]] = {}
        self._task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        joint_names = ManaConfig.get().get_all_joint_names()
        self._simulators = {name: MockJointSimulator(name) for name in joint_names}
        self._task = asyncio.create_task(self._mock_publish_loop())

    async def subscribe(
        self,
        topic: str,
        msg_type: str,
        callback: Callable[[Dict[str, Any]], Any],
        throttle_rate: int = 0,
    ) -> None:
        self._subscribers.setdefault(topic, []).append(callback)

    async def call_service(self, service: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "values": {}}

    async def _mock_publish_loop(self) -> None:
        dt = self._PUBLISH_INTERVAL_S
        while True:
            names: List[str] = []
            positions: List[float] = []
            velocities: List[float] = []
            efforts: List[float] = []

            for name, sim in self._simulators.items():
                sample = sim.step(dt)
                names.append(name)
                positions.append(sample["position"])
                velocities.append(sample["velocity"])
                efforts.append(sample["effort"])

            joint_state_msg = {
                "name": names,
                "position": positions,
                "velocity": velocities,
                "effort": efforts,
            }
            for cb in self._subscribers.get("/joint_states", []):
                result = cb(joint_state_msg)
                if asyncio.iscoroutine(result):
                    await result

            await asyncio.sleep(dt)

    async def disconnect(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            # Keep task reference (done/cancelled) — do not set to None


def create_dds_bridge() -> DDSBridge:
    """Factory: returns MockDDSBridge or RealDDSBridge based on config."""
    if ManaConfig.get().is_mock_mode():
        return MockDDSBridge()
    return RealDDSBridge()
