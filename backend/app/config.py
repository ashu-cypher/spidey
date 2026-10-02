from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    spidey_provider: str = "rule_based"  # reads SPIDEY_PROVIDER (case-insensitive)
    openai_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    database_url: str = ""
    vector_backend: str = "local"
    spidey_debug: bool = False
    persona_name: str = "MEW"  # reads PERSONA_NAME


settings = Settings()
