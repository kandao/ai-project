from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    KAFKA_BOOTSTRAP_SERVERS: str
    KAFKA_GROUP_ID: str = "worker-group"
    KAFKA_TOPIC_INGEST: str = "doc.ingest"
    KAFKA_TOPIC_DELETE: str = "doc.delete"

    DATABASE_URL: str

    EMBEDDING_PROVIDER: str = "voyage"
    EMBEDDING_MODEL: str = "voyage-3"
    VOYAGE_API_KEY: str = ""
    COHERE_API_KEY: str = ""

    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64

    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_PATH: str = "/data/uploads"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
