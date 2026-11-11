"""Adapter registry. Adapter modules are imported lazily on first lookup."""

from importlib import import_module

from daily_job_tool.adapters.base import Adapter

ADAPTER_MODULES = ["greenhouse", "lever", "ashby"]

_REGISTRY: dict[str, type[Adapter]] = {}
_loaded = False


def register[T: type[Adapter]](cls: T) -> T:
    _REGISTRY[cls.name] = cls
    return cls


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for mod in ADAPTER_MODULES:
        full = f"daily_job_tool.adapters.{mod}"
        try:
            import_module(full)
        except ModuleNotFoundError as exc:
            if exc.name != full:  # a real missing dependency inside the module
                raise


def get_adapter(name: str) -> Adapter:
    _load()
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise KeyError(f"unknown adapter {name!r}; known: {sorted(_REGISTRY)}") from None


def all_adapters() -> list[Adapter]:
    _load()
    return [cls() for _, cls in sorted(_REGISTRY.items())]


def get_tier(name: str) -> int:
    """Source tier of a registered adapter (lower is better); 99 for unknown names."""
    _load()
    cls = _REGISTRY.get(name)
    return cls.tier if cls is not None else 99
