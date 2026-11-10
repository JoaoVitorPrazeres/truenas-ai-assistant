import asyncio
import logging
import math
from collections.abc import Callable
from typing import Any

from ia_admin.agent.tools import execute_tool
from ia_admin.config import Settings


log = logging.getLogger("ia-admin-telegram")


class TemperatureMonitor:
    def __init__(
        self,
        settings: Settings,
        tool_functions: dict[str, Callable[..., Any]],
    ):
        self.settings = settings
        self.tool_functions = tool_functions
        self._active_critical: dict[str, tuple[float, float, str]] = {}

    async def run(self, bot: Any) -> None:
        while True:
            try:
                await self.check_once(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Falha inesperada no monitor de temperatura")
            await asyncio.sleep(
                self.settings.temperature_monitor_interval_minutes * 60
            )

    async def check_once(self, bot: Any) -> None:
        sources = (
            ("get_disk_temperatures", "disco", self.settings.disk_critical_temp_c),
            ("get_system_temperatures", "CPU", self.settings.system_critical_temp_c),
        )
        for tool_name, sensor_type, configured_threshold in sources:
            result = await asyncio.to_thread(
                execute_tool, tool_name, {}, self.tool_functions
            )
            if isinstance(result, dict) and result.get("error"):
                log.error(
                    "Monitoramento de temperatura não consultou %s: %s",
                    sensor_type,
                    result["error"],
                )
                continue
            if not isinstance(result, list):
                log.error(
                    "Monitoramento de temperatura recebeu formato inválido de %s",
                    sensor_type,
                )
                continue

            for item in result:
                if not isinstance(item, dict):
                    continue
                temperature = item.get("temperature")
                if (
                    not isinstance(temperature, (int, float))
                    or isinstance(temperature, bool)
                    or not math.isfinite(temperature)
                    or str(item.get("unit") or "C").upper() not in {"C", "°C"}
                ):
                    continue

                threshold = configured_threshold
                if sensor_type == "disco":
                    reported_threshold = item.get("critical_temperature")
                    if (
                        isinstance(reported_threshold, (int, float))
                        and not isinstance(reported_threshold, bool)
                        and math.isfinite(reported_threshold)
                        and reported_threshold > 0
                    ):
                        threshold = min(threshold, float(reported_threshold))

                sensor_name = str(item.get("name") or "sensor sem nome")
                key = f"{sensor_type}:{sensor_name}"
                current = (float(temperature), threshold, sensor_type)
                previous = self._active_critical.get(key)
                if temperature >= threshold and previous is None:
                    text = (
                        f"🚨 Temperatura crítica: {sensor_type} {sensor_name} "
                        f"está a {temperature:.1f} °C "
                        f"(limite: {threshold:.1f} °C)."
                    )
                    if await self._notify_all(bot, text):
                        self._active_critical[key] = current
                elif temperature >= threshold:
                    self._active_critical[key] = current
                elif previous is not None:
                    _, previous_threshold, _ = previous
                    text = (
                        f"✅ Temperatura normalizada: {sensor_type} {sensor_name} "
                        f"está a {temperature:.1f} °C "
                        f"(limite crítico: {previous_threshold:.1f} °C)."
                    )
                    if await self._notify_all(bot, text):
                        self._active_critical.pop(key, None)

    async def _notify_all(self, bot: Any, text: str) -> bool:
        succeeded = True
        for chat_id in self.settings.allowed_chat_ids:
            try:
                await bot.send_message(chat_id=int(chat_id), text=text)
            except (TypeError, ValueError):
                log.error(
                    "ID de chat inválido para notificação automática de temperatura"
                )
                succeeded = False
            except Exception:
                log.exception(
                    "Falha ao enviar alerta automático de temperatura para um chat"
                )
                succeeded = False
        return succeeded
