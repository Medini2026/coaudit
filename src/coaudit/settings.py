from pathlib import Path

from coaudit.contracts import Config

DEFAULT_CONFIG_PATH = Path("config.json")


def load_config(path=DEFAULT_CONFIG_PATH) -> Config:
    file = Path(path)
    if not file.exists():
        return Config()
    return Config.model_validate_json(file.read_text(encoding="utf-8"))


def save_config(config: Config, path=DEFAULT_CONFIG_PATH) -> None:
    Path(path).write_text(config.model_dump_json(indent=2), encoding="utf-8")
