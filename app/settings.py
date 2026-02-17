"""Application configuration from environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings

from app.version import __version__


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # API Keys
    ANTHROPIC_API_KEY: str = ""

    # Model configuration
    CLAUDE_MODEL: str = "claude-sonnet-4-5-20241022"

    # Paths
    DATABASE_PATH: Path = Path("data/app.db")
    AGREEMENTS_DIR: Path = Path("data/agreements")
    INDEX_DIR: Path = Path("data/index")

    # Search settings
    MAX_RETRIEVAL_RESULTS: int = 10

    # Auto-update settings
    AUTO_UPDATE_ENABLED: bool = False
    GITHUB_REPO: str = "JCHanratty/CASearch"
    APP_VERSION: str = __version__

    # Bug report GitHub integration
    BUGREPORT_CREATE_ISSUE: bool = False
    BUGREPORT_GITHUB_REPO: str = ""  # e.g. owner/repo
    BUGREPORT_GITHUB_TOKEN: str = ""

    # Branding
    ORGANIZATION_NAME: str = ""
    LEGAL_DISCLAIMER: str = "This tool provides informational summaries. Always consult the original agreement for authoritative language."

    # Suggested prompts for Q&A
    SUGGESTED_PROMPTS: list[str] = [
        "What is the overtime rate in [LocalName]?",
        "Show me the sick leave policy for [LocalName].",
        "How many vacation days are provided after 5 years?",
        "What is the grievance procedure?",
        "What are the scheduling/overtime rules?",
        "Summarize the pension/benefits section.",
    ]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
