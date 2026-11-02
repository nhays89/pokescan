from __future__ import annotations
import logging
from pathlib import Path
import yaml
from pydantic import ValidationError
from .models import AppConfig

log = logging.getLogger(__name__)


class ConfigLoader:
    """Re-reads the YAML when its mtime changes. A bad edit never kills the daemon:
    we log the error and keep serving the last good config."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._mtime: float | None = None
        self._config: AppConfig | None = None

    def current(self) -> AppConfig:
        try:
            mtime = self.path.stat().st_mtime
            if mtime != self._mtime:
                data = yaml.safe_load(self.path.read_text())
                self._config = AppConfig.model_validate(data)
                self._mtime = mtime
                log.info("Loaded config: %d searches", len(self._config.searches))
        except (OSError, yaml.YAMLError, ValidationError) as e:
            if self._config is None:
                raise  # nothing to fall back on at startup
            try:
                self._mtime = self.path.stat().st_mtime  # don't re-log every tick
            except OSError:
                pass
            log.error("Config reload failed, keeping last good config: %s", e)
        assert self._config is not None
        return self._config
