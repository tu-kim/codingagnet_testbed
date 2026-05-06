from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    opencode_url: str = Field("http://127.0.0.1:4096")
    opencode_password: str = ""

    dynamo_base_url: str = "http://127.0.0.1:8000/v1"
    dynamo_api_key: str = "local"
    model_name: str = ""

    jaeger_query_url: str = "http://127.0.0.1:16686"

    workspace_root: Path = Path("./results/_workspaces")
