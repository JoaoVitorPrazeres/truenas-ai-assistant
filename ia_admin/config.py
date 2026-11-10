from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    truenas_url: str
    truenas_username: str
    truenas_api_key: str
    truenas_ca_bundle: str
    ollama_model: str
    ollama_host: str
    telegram_bot_token: str
    allowed_chat_ids: frozenset[str]
    temperature_monitor_interval_minutes: int = 5
    disk_critical_temp_c: float = 50.0
    system_critical_temp_c: float = 85.0

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()

        values = {
            "TRUENAS_URL": os.environ.get("TRUENAS_URL", "").rstrip("/"),
            "TRUENAS_USERNAME": os.environ.get("TRUENAS_USERNAME", ""),
            "TRUENAS_API_KEY": os.environ.get("TRUENAS_API_KEY", ""),
            "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            "TELEGRAM_ALLOWED_CHAT_ID": os.environ.get(
                "TELEGRAM_ALLOWED_CHAT_ID", ""
            ),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise SystemExit(f"Defina as variáveis obrigatórias: {', '.join(missing)}.")

        allowed_chat_ids = frozenset(
            chat_id.strip()
            for chat_id in values["TELEGRAM_ALLOWED_CHAT_ID"].split(",")
            if chat_id.strip()
        )
        if not allowed_chat_ids:
            raise SystemExit(
                "Defina TELEGRAM_ALLOWED_CHAT_ID com um ou mais IDs de chat autorizados."
            )

        try:
            temperature_monitor_interval_minutes = int(
                os.environ.get("TEMPERATURE_MONITOR_INTERVAL_MINUTES", "5")
            )
            disk_critical_temp_c = float(
                os.environ.get("DISK_CRITICAL_TEMP_C", "50")
            )
            system_critical_temp_c = float(
                os.environ.get("SYSTEM_CRITICAL_TEMP_C", "85")
            )
        except ValueError as exc:
            raise SystemExit(
                "Configure TEMPERATURE_MONITOR_INTERVAL_MINUTES, "
                "DISK_CRITICAL_TEMP_C e SYSTEM_CRITICAL_TEMP_C com valores numéricos."
            ) from exc

        if temperature_monitor_interval_minutes < 5:
            raise SystemExit(
                "TEMPERATURE_MONITOR_INTERVAL_MINUTES deve ser pelo menos 5, "
                "pois o TrueNAS atualiza temperaturas dos discos nesse intervalo."
            )
        if not 0 < disk_critical_temp_c <= 120:
            raise SystemExit("DISK_CRITICAL_TEMP_C deve estar entre 0 e 120 °C.")
        if not 0 < system_critical_temp_c <= 150:
            raise SystemExit("SYSTEM_CRITICAL_TEMP_C deve estar entre 0 e 150 °C.")

        return cls(
            truenas_url=values["TRUENAS_URL"],
            truenas_username=values["TRUENAS_USERNAME"],
            truenas_api_key=values["TRUENAS_API_KEY"],
            truenas_ca_bundle=os.environ.get("TRUENAS_CA_BUNDLE", "").strip(),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:1.7b"),
            ollama_host=os.environ.get(
                "OLLAMA_HOST", "http://127.0.0.1:11434"
            ).rstrip("/"),
            telegram_bot_token=values["TELEGRAM_BOT_TOKEN"],
            allowed_chat_ids=allowed_chat_ids,
            temperature_monitor_interval_minutes=temperature_monitor_interval_minutes,
            disk_critical_temp_c=disk_critical_temp_c,
            system_critical_temp_c=system_critical_temp_c,
        )
