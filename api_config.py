"""Cấu hình endpoint Anthropic API tập trung một chỗ."""
import os
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class APIConfig:
    api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    base_url: str = field(default_factory=lambda: os.getenv("ANTHROPIC_BASE_URL",
                                                            DEFAULT_BASE_URL))
    model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL",
                                                         DEFAULT_MODEL))

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def to_client_kwargs(self) -> dict:
        """Return kwargs để pass vào anthropic.Anthropic().

        SDK anthropic tự nối '/v1/messages' vào base_url; nếu người dùng lỡ lưu
        base_url có sẵn '/v1' thì sẽ thành '/v1/v1/messages' (404). Cắt bỏ đuôi
        '/v1' để chống lỗi cấu hình phổ biến này.
        """
        base = (self.base_url or DEFAULT_BASE_URL).rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        kwargs = {"base_url": base}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return kwargs

    @classmethod
    def from_cli(
        cls,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> "APIConfig":
        """CLI option thắng env var; env var thắng default."""
        cfg = cls()
        if api_key:
            cfg.api_key = api_key
        if base_url:
            cfg.base_url = base_url
        if model:
            cfg.model = model
        return cfg
