from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aws_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("AWS_DEFAULT_REGION", "AWS_REGION"),
    )
    # Read from .env (or the environment) and passed explicitly to the Bedrock
    # client. boto3 does NOT read .env on its own, so these fields are what make
    # a pre-filled .env "just work" for a non-technical user. If left unset,
    # boto3's default credential chain (env vars / ~/.aws / IAM role) is used.
    aws_access_key_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_ACCESS_KEY_ID", "LINGUA_AWS_ACCESS_KEY_ID"),
    )
    aws_secret_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_SECRET_ACCESS_KEY", "LINGUA_AWS_SECRET_ACCESS_KEY"),
    )
    bedrock_model_id: str = Field(
        default="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        validation_alias="BEDROCK_MODEL_ID",
    )
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    # Output is ~7-9 JSON tokens per input char of Greek, so a chunk much larger
    # than this risks the 16000 max_tokens cap, which truncates the call and
    # triggers the (sequential) recursive re-split in BedrockClaudeProvider.
    # 1200 chars (~11k output tokens) leaves comfortable margin under the cap and
    # yields more, smaller chunks that run in parallel.
    max_chunk_chars: int = Field(default=1200, validation_alias="LINGUA_MAX_CHUNK_CHARS")
    max_workers: int = Field(
        default=8,
        validation_alias="LINGUA_MAX_WORKERS",
        description="Max concurrent Bedrock calls (per chapter fan-out and per chunk fan-out).",
    )
    debug_tracebacks: bool = Field(default=False, validation_alias="LINGUA_DEBUG_TRACEBACKS")
    bedrock_timeout_seconds: int = Field(
        default=1200,
        validation_alias="BEDROCK_TIMEOUT_SECONDS",
    )


def get_settings() -> Settings:
    return Settings()
