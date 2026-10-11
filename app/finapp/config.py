import base64
import os
import pathlib
from zoneinfo import ZoneInfo


class Config:
    def __init__(self, env=None):
        e = os.environ if env is None else env
        self.data_dir = pathlib.Path(e.get("FINAPP_DATA_DIR", "./data"))
        self.host = e.get("FINAPP_HOST", "127.0.0.1")
        self.port = int(e.get("FINAPP_PORT", "8100"))
        self.tz = ZoneInfo(e.get("FINAPP_TZ", "America/Toronto"))
        self.require_ts_header = e.get("FINAPP_REQUIRE_TS_HEADER", "1") == "1"
        self.ts_logins = {x.strip().lower() for x in e.get("FINAPP_TS_LOGINS", "").split(",") if x.strip()}
        self.cookie_secure = e.get("FINAPP_COOKIE_SECURE", "1") == "1"
        self.vapid_pem = e.get("FINAPP_VAPID_PEM", "")
        self.vapid_subject = e.get("FINAPP_VAPID_SUBJECT", "mailto:admin@example.invalid")
        k = e.get("FINAPP_FILE_KEY", "")
        self.file_key = base64.b64decode(k) if k else None
        self.anthropic_key = e.get("ANTHROPIC_API_KEY", "")
        self.llm_model = e.get("FINAPP_LLM_MODEL", "claude-sonnet-5")
        self.llm_cap_cents = int(e.get("FINAPP_LLM_MONTHLY_CAP_CENTS", "500"))
        # Assumed list prices in cents per million tokens; override via env if pricing differs.
        self.llm_in_cents_per_mtok = float(e.get("FINAPP_LLM_IN_CENTS_PER_MTOK", "300"))
        self.llm_out_cents_per_mtok = float(e.get("FINAPP_LLM_OUT_CENTS_PER_MTOK", "1500"))

    @property
    def db_path(self):
        return self.data_dir / "finapp.db"

    @property
    def files_dir(self):
        return self.data_dir / "files"

    @property
    def snapshots_dir(self):
        return self.data_dir / "snapshots"


_cfg = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg


def set_config(cfg: Config):
    global _cfg
    _cfg = cfg
