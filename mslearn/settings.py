from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MSL_", extra="ignore")

    data_dir: Path = Path("data")
    profiles_path: Path = Path("profiles.yaml")
    redis_url: str = "redis://localhost:6380/0"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "learnsys"
    openrouter_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    claude_binary: str = "claude"
    # Whisper model for audio / caption-less-video transcription. "small" +
    # int8 keeps the resident footprint modest (~0.5 GB) so it can coexist
    # with Ollama on an 18 GB machine; transcription is serialized (see
    # SerializingTranscriber) so two ingest slots never load two at once.
    whisper_model: str = "small"

    @property
    def ops_db(self) -> Path:
        return self.data_dir / "ops.db"


def get_settings() -> Settings:
    return Settings()
