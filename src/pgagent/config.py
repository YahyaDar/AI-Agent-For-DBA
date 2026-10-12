from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    primary_dsn: str = "postgresql://postgres:postgres@localhost:5432/appdb"
    replica_dsn: str = "postgresql://postgres:postgres@localhost:5433/appdb"
    # step 3 adds a read-only role; step 6 adds a separate executor role
    ro_dsn: str = ""
    exec_dsn: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

settings = Settings()
