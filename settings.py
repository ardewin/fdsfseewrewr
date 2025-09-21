from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str
    database_url: str
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

# Create a global instance
settings = Settings() 