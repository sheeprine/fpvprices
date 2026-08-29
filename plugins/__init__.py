from abc import ABC, abstractmethod
from typing import Optional
import re


class SitePlugin(ABC):
    name: str
    display_name: str
    currency: str
    tracks_stock: bool

    @abstractmethod
    def can_handle(self, url: str) -> bool: ...

    @abstractmethod
    def extract_handle(self, url: str) -> Optional[str]: ...

    @abstractmethod
    def fetch_product(self, handle: str) -> Optional[dict]: ...

    @abstractmethod
    def parse_product(self, raw: dict) -> dict: ...

    def extract_pack_count(self, name: str) -> int:
        m = re.search(r'(\d+)\s*(?:pcs|pack)', name, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            return n if n > 1 else 1
        return 1


_registry: dict[str, "SitePlugin"] = {}


def register(plugin: "SitePlugin") -> None:
    _registry[plugin.name] = plugin


def get_plugin_for_url(url: str) -> Optional["SitePlugin"]:
    for plugin in _registry.values():
        if plugin.can_handle(url):
            return plugin
    return None


def get_plugin(name: str) -> Optional["SitePlugin"]:
    return _registry.get(name)


def get_all_plugins() -> dict[str, "SitePlugin"]:
    return dict(_registry)


# Register built-in plugins — placed at the bottom so SitePlugin is defined
# before the submodules import it (safe circular-import pattern).
from plugins.mepsking import MepskingPlugin  # noqa: E402
from plugins.ampow import AmpowPlugin  # noqa: E402
from plugins.dfr import DFRPlugin  # noqa: E402
from plugins.thobby import THobbyPlugin  # noqa: E402

register(MepskingPlugin())
register(AmpowPlugin())
register(DFRPlugin())
register(THobbyPlugin())
