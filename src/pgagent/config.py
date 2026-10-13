from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    primary_dsn: str = "postgresql://postgres:postgres@localhost:5432/appdb"
    replica_dsn: str = "postgresql://postgres:postgres@localhost:5433/appdb"
    ro_dsn: str = "postgresql://agent_ro:agent_ro_pw@localhost:5432/appdb"
    # PROTOTYPE: executor uses the superuser. Harden with a dedicated role later.
    exec_dsn: str = "postgresql://postgres:postgres@localhost:5432/appdb"
    llm_api_key: str = ""
    llm_model: str = ""

    # detector thresholds
    bloat_dead_ratio: float = 0.2
    bloat_min_dead: int = 10_000
    stale_mod_ratio: float = 0.2
    stale_min_mod: int = 1_000
    idle_txn_seconds: int = 30
    lock_wait_seconds: int = 5
    repl_lag_bytes: int = 8 * 1024 * 1024

    # policy
    max_actions_per_hour: int = 20
    reindex_max_bytes: int = 1_000_000_000
    cooldown_s: int = 120
    dry_run: bool = False

settings = Settings()
