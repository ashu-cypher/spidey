from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    spidey_provider: str = "rule_based"  # reads SPIDEY_PROVIDER (case-insensitive)
    openai_api_key: str = ""
    # OpenAI-compatible base URL — point at Groq
    # (https://api.groq.com/openai/v1) or OpenRouter
    # (https://openrouter.ai/api/v1) for fast free models.
    # Defaults to OpenAI. Reads OPENAI_BASE_URL.
    openai_base_url: str = "https://api.openai.com/v1"  # reads OPENAI_BASE_URL
    openai_model: str = "gpt-4o-mini"  # reads OPENAI_MODEL
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
    # WhatsApp Cloud API integration: token, phone number ID and the
    # single allowed recipient live ONLY here (backend env) or in the
    # backend-only config file (~/.mew_whatsapp.json) — never in
    # frontend code. verify_token/app_secret are for the webhook.
    whatsapp_token: str = ""  # reads WHATSAPP_TOKEN
    whatsapp_phone_number_id: str = ""  # reads WHATSAPP_PHONE_NUMBER_ID
    whatsapp_recipient: str = ""  # reads WHATSAPP_RECIPIENT
    whatsapp_verify_token: str = ""  # reads WHATSAPP_VERIFY_TOKEN
    whatsapp_app_secret: str = ""  # reads WHATSAPP_APP_SECRET


settings = Settings()

# Load backend-only LLM config file (~/.mew_llm.json) if present.
# Env vars take precedence (pydantic already read them); the file fills
# gaps so the Settings UI can configure the API without editing .env.
try:
    import json as _json
    from pathlib import Path as _Path

    _llm_cfg = _Path.home() / ".mew_llm.json"
    if _llm_cfg.exists():
        _data = _json.loads(_llm_cfg.read_text())
        if isinstance(_data, dict):
            import os as _os

            if not _os.environ.get("OPENAI_API_KEY") and _data.get("openai_api_key"):
                settings.openai_api_key = str(_data["openai_api_key"])
            if not _os.environ.get("OPENAI_BASE_URL") and _data.get("openai_base_url"):
                settings.openai_base_url = str(_data["openai_base_url"])
            if not _os.environ.get("OPENAI_MODEL") and _data.get("openai_model"):
                settings.openai_model = str(_data["openai_model"])
except Exception:
    pass
