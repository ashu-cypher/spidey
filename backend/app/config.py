from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    spidey_provider: str = "rule_based"  # reads SPIDEY_PROVIDER (case-insensitive)
    openai_api_key: str = ""
    # MEW real-agent transformation: Ollama is the default intelligence.
    # 127.0.0.1 (not localhost) avoids slow IPv6/localhost resolution stalls.
    ollama_base_url: str = "http://127.0.0.1:11434"  # reads OLLAMA_BASE_URL
    ollama_model: str = "qwen3:0.6b"  # reads OLLAMA_MODEL
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    database_url: str = ""
    vector_backend: str = "local"
    spidey_debug: bool = False
    persona_name: str = "MEW"  # reads PERSONA_NAME
    # Telegram integration (spec 18-19): bot token and chat ID live ONLY
    # here (backend env), never in frontend code.
    telegram_bot_token: str = ""  # reads TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = ""  # reads TELEGRAM_CHAT_ID


settings = Settings()
