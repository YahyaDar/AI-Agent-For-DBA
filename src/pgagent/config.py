from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Target database connections (bluebox)
    ro_dsn: str = "postgresql://agent_ro:agent_ro_pw@localhost:5432/bluebox"
    exec_dsn: str = "postgresql://agent_exec:agent_exec_pw@localhost:5432/bluebox"

    # Store database connection (appdb)
    store_dsn: str = "postgresql://agent_store:agent_store_pw@localhost:5432/appdb"
    llm_api_key: str = ""
    llm_model: str = ""

    # detector thresholds
    bloat_dead_ratio: float = 0.2
    bloat_min_dead: int = 10_000
    stale_mod_ratio: float = 0.2
    stale_min_mod: int = 1_000
    idle_txn_seconds: int = 30
    kill_requires_blocking: bool = False   # True = only kill idle-in-txn sessions that block others
    lock_wait_seconds: int = 5
    repl_lag_bytes: int = 8 * 1024 * 1024

    # policy
    max_actions_per_hour: int = 20
    reindex_max_bytes: int = 1_000_000_000
    cooldown_s: int = 120
    dry_run: bool = False

settings = Settings()
