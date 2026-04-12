from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    KAFKA_BOOTSTRAP_SERVERS: str
    KAFKA_GROUP_ID: str = "worker-group"
    KAFKA_TOPIC_INGEST: str = "doc.ingest"
    KAFKA_TOPIC_DELETE: str = "doc.delete"

    DATABASE_URL: str

    EMBEDDING_PROVIDER: str = "openai"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    # DO NOT put real API keys here — these are schema declarations only (default = empty).
    # Set actual values in worker/.env (gitignored) or as Docker environment variables.
    # See worker/.env.example for the template.
    OPENAI_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""
    COHERE_API_KEY: str = ""

    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64

    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_PATH: str = "/data/uploads"


settings = Settings()
