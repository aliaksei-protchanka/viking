"""Configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    base_url: str
    email: str | None
    password: str | None
    llm_provider: str  # "openai" | "github" | "anthropic"
    llm_model: str
    openai_api_key: str | None
    github_token: str | None
    anthropic_api_key: str | None
    state_dir: Path
    prompt_file: Path | None

    @classmethod
    def load(cls) -> "Settings":
        state_dir = Path(os.path.expanduser(os.getenv("VIKING_STATE_DIR", "~/.viking")))
        state_dir.mkdir(parents=True, exist_ok=True)
        provider = os.getenv("VIKING_LLM_PROVIDER", "github").lower()
        default_models = {
            "github": "openai/gpt-4o-mini",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-sonnet-4-5",
        }
        default_model = default_models.get(provider, "openai/gpt-4o-mini")
        prompt_env = os.getenv("VIKING_PROMPT_FILE")
        prompt_file = Path(os.path.expanduser(prompt_env)) if prompt_env else None
        return cls(
            base_url=os.getenv("VIKING_BASE_URL", "https://panel.kuchniavikinga.pl").rstrip("/"),
            email=os.getenv("VIKING_EMAIL") or None,
            password=os.getenv("VIKING_PASSWORD") or None,
            llm_provider=provider,
            llm_model=os.getenv("VIKING_LLM_MODEL", default_model),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            state_dir=state_dir,
            prompt_file=prompt_file,
        )

    def llm_credential(self) -> str:
        if self.llm_provider == "openai":
            if not self.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY is not set (VIKING_LLM_PROVIDER=openai)")
            return self.openai_api_key
        if self.llm_provider == "github":
            if not self.github_token:
                raise RuntimeError(
                    "GITHUB_TOKEN is not set (VIKING_LLM_PROVIDER=github). "
                    "Create a PAT with `models:read` scope."
                )
            return self.github_token
        if self.llm_provider == "anthropic":
            if not self.anthropic_api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set (VIKING_LLM_PROVIDER=anthropic)."
                )
            return self.anthropic_api_key
        raise RuntimeError(f"Unknown VIKING_LLM_PROVIDER={self.llm_provider!r}")
