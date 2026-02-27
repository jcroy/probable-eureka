"""Profile persistence — load and save SiteProfile YAML files.

Profiles live in two locations:
1. Bundled profiles: shipped with webcollector in profiles/bundled/*.yaml
2. User profiles: saved to ~/.webcollector/profiles/*.yaml (created by LLM)

User profiles take precedence over bundled profiles with the same name.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from webcollector.profiles.models import SiteProfile

logger = structlog.get_logger(__name__)

_BUNDLED_DIR = Path(__file__).parent / "bundled"
_USER_DIR = Path.home() / ".webcollector" / "profiles"


class ProfileStore:
    """Loads and persists SiteProfile YAML files."""

    def __init__(
        self,
        bundled_dir: Path | None = None,
        user_dir: Path | None = None,
    ) -> None:
        self._bundled_dir = bundled_dir or _BUNDLED_DIR
        self._user_dir = user_dir or _USER_DIR
        self._profiles: dict[str, SiteProfile] = {}
        self._loaded = False

    def load_all(self) -> list[SiteProfile]:
        """Load all profiles from bundled + user directories.

        User profiles override bundled ones with the same name.
        """
        self._profiles.clear()

        # Load bundled first
        for profile in self._load_dir(self._bundled_dir):
            self._profiles[profile.name] = profile

        # User profiles override bundled
        for profile in self._load_dir(self._user_dir):
            self._profiles[profile.name] = profile

        self._loaded = True
        logger.info("profiles_loaded", count=len(self._profiles))
        return list(self._profiles.values())

    def get(self, name: str) -> SiteProfile | None:
        """Get a profile by name. Loads all profiles if not yet loaded."""
        if not self._loaded:
            self.load_all()
        return self._profiles.get(name)

    def all(self) -> list[SiteProfile]:
        """Return all loaded profiles."""
        if not self._loaded:
            self.load_all()
        return list(self._profiles.values())

    def save(self, profile: SiteProfile) -> Path:
        """Save a profile to the user directory. Returns the file path."""
        self._user_dir.mkdir(parents=True, exist_ok=True)
        path = self._user_dir / f"{profile.name}.yaml"

        data = profile.model_dump(mode="json")
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        self._profiles[profile.name] = profile
        logger.info("profile_saved", name=profile.name, path=str(path))
        return path

    def _load_dir(self, directory: Path) -> list[SiteProfile]:
        """Load all .yaml/.yml profiles from a directory."""
        profiles: list[SiteProfile] = []
        if not directory.is_dir():
            return profiles

        for path in sorted(directory.glob("*.y*ml")):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    continue
                profile = SiteProfile(**data)
                profiles.append(profile)
                logger.debug("profile_loaded", name=profile.name, path=str(path))
            except Exception:
                logger.warning("profile_load_failed", path=str(path), exc_info=True)

        return profiles
