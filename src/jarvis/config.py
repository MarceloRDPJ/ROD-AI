import os
import yaml
import pytz
from dotenv import load_dotenv

load_dotenv()

class Config:
    # ==================================================
    # TIMEZONE
    # ==================================================
    TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
    TZ = pytz.timezone(TIMEZONE)

    # ==================================================
    # TUNING / MAGIC NUMBERS
    # ==================================================
    INTENT_CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", 0.88))
    SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", 30))
    HYDRATION_MIN_INTERVAL_MINUTES = int(os.getenv("HYDRATION_MIN_INTERVAL_MINUTES", 10))

    # ==================================================
    # TELEGRAM
    # ==================================================
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    raw_id = os.getenv("ALLOWED_USER_ID", "0")
    ALLOWED_USER_ID = int(raw_id) if raw_id.strip() else 0

    # ==================================================
    # LOCAL WEB TOOLS (FREE/PUBLIC SOURCES)
    # ==================================================
    LOCAL_WEB_TOOLS_ENABLED = os.getenv("LOCAL_WEB_TOOLS_ENABLED", "true").strip().lower() == "true"
    WEB_FETCH_TIMEOUT_SECONDS = int(os.getenv("WEB_FETCH_TIMEOUT_SECONDS", 10))
    WEB_FETCH_MAX_CHARS = int(os.getenv("WEB_FETCH_MAX_CHARS", 12000))
    WEB_USER_AGENT = os.getenv("WEB_USER_AGENT", "RODDoCerrado/1.0")
    RSS_FEEDS_ENABLED = os.getenv("RSS_FEEDS_ENABLED", "true").strip().lower() == "true"
    RSS_FEEDS = [feed.strip() for feed in os.getenv("RSS_FEEDS", "").split(",") if feed.strip()]
    CURRENT_INFO_BRASILEIRAO_URL = os.getenv("CURRENT_INFO_BRASILEIRAO_URL", "")

    # ==================================================
    # NETWORK / SYSTEM
    # ==================================================
    PC_MAC = os.getenv("PC_MAC", "").strip()
    PC_IP = os.getenv("PC_IP", "").strip()

    # ==================================================
    # DASHBOARD ADMIN (independente do Telegram)
    # ==================================================
    DASHBOARD_AUTH_ENABLED = os.getenv("DASHBOARD_AUTH_ENABLED", "true").strip().lower() == "true"
    DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "marcelo").strip()
    DASHBOARD_PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
    DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET", "").strip()
    DASHBOARD_SESSION_TTL_SECONDS = int(os.getenv("DASHBOARD_SESSION_TTL_SECONDS", 28800))
    DASHBOARD_COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "false").strip().lower() == "true"

    # ==================================================
    # CONFIG.YAML (COMPORTAMENTO)
    # ==================================================
    YAML_CONFIG = {}
    try:
        with open(os.path.join(os.path.dirname(__file__), "config.yaml"), "r") as f:
            YAML_CONFIG = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[WARN] Não foi possível carregar config.yaml: {e}")

    @staticmethod
    def get(path, default=None):
        """
        Get config value by dot notation
        Ex: Config.get("system.retry_attempts")
        """
        keys = path.split(".")
        val = Config.YAML_CONFIG
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
        return val if val is not None else default

    # ==================================================
    # VALIDATIONS (OPCIONAL MAS PROFISSIONAL)
    # ==================================================
    @staticmethod
    def validate():
        missing = []

        if not Config.TELEGRAM_TOKEN:
            missing.append("TELEGRAM_TOKEN")

        if not Config.ALLOWED_USER_ID:
            missing.append("ALLOWED_USER_ID")

        if missing:
            raise RuntimeError(
                f"Configuração incompleta. Variáveis ausentes: {', '.join(missing)}"
            )
