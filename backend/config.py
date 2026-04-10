from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://docqa:docqa@localhost/docqa",
        description="Async PostgreSQL connection URL",
    )

    # Redis
    REDIS_URL: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = Field(
        default="localhost:9092",
        description="Comma-separated Kafka bootstrap servers",
    )

    # JWT
    JWT_SECRET: str = Field(
        default="change-me-in-production",
        description="Secret key for JWT signing",
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        description="JWT signing algorithm",
    )
    JWT_EXPIRY_MINUTES: int = Field(
        default=60,
        description="JWT token expiry in minutes",
    )

    # File storage
    STORAGE_BACKEND: str = Field(
        default="local",
        description="Storage backend: 'local' or 's3'",
    )
    STORAGE_LOCAL_PATH: str = Field(
        default="/data/uploads",
        description="Local filesystem path for file storage",
    )
    S3_BUCKET: str = Field(
        default="docqa-uploads",
        description="S3 bucket name (if STORAGE_BACKEND=s3)",
    )
    AWS_ACCESS_KEY_ID: str = Field(
        default="",
        description="AWS access key ID (if STORAGE_BACKEND=s3)",
    )
    AWS_SECRET_ACCESS_KEY: str = Field(
        default="",
        description="AWS secret access key (if STORAGE_BACKEND=s3)",
    )

    # Limits
    MAX_UPLOAD_SIZE_MB: int = Field(
        default=50,
        description="Maximum upload file size in megabytes",
    )
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = Field(
        default=30,
        description="Rate limit: maximum requests per minute per user",
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
