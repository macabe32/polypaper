from __future__ import annotations

from importlib import import_module
from typing import Any

from .models.always_pass import AlwaysPassModel
from .models.base import BaseModel
from .models.kelly_gbm import KellyGBMModel
from .sizers.base import BaseSizer
from .sizers.equal_weight import EqualWeightSizer
from .sizers.fixed import FixedSizer
from .sizers.kelly import KellySizer


BUILTIN_MODELS: dict[str, type[BaseModel]] = {
    "always_pass": AlwaysPassModel,
    "kelly_gbm": KellyGBMModel,
}

BUILTIN_SIZERS: dict[str, type[BaseSizer]] = {
    "fixed": FixedSizer,
    "equal_weight": EqualWeightSizer,
    "kelly": KellySizer,
}


def _load_from_path(path: str) -> Any:
    # format: package.module:ClassName
    module_name, class_name = path.split(":")
    module = import_module(module_name)
    return getattr(module, class_name)


def make_model(name: str, kwargs: dict[str, Any] | None = None) -> BaseModel:
    kwargs = kwargs or {}
    if ":" in name:
        cls = _load_from_path(name)
        return cls(**kwargs)
    if name not in BUILTIN_MODELS:
        raise ValueError(f"Unknown model '{name}'")
    return BUILTIN_MODELS[name](**kwargs)


def make_sizer(name: str, kwargs: dict[str, Any] | None = None) -> BaseSizer:
    kwargs = kwargs or {}
    if ":" in name:
        cls = _load_from_path(name)
        return cls(**kwargs)
    if name not in BUILTIN_SIZERS:
        raise ValueError(f"Unknown sizer '{name}'")
    return BUILTIN_SIZERS[name](**kwargs)
