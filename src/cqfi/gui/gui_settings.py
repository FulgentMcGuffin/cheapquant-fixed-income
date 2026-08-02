from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from plot_settings_dialog import GRAPH_SETTINGS_DEFAULTS

SETTINGS_FILENAME = "mcp-data-gui.yaml"

DEFAULT_DOWNLOAD_SETTINGS = {
    "format": "csv",
    "headers": True,
    "directory": None,
}

DEFAULT_SETTINGS: dict = {
    "ui_theme": "Fluent Dark",
    "plot_settings": dict(GRAPH_SETTINGS_DEFAULTS),
    "download_settings": dict(DEFAULT_DOWNLOAD_SETTINGS),
}


def settings_path() -> Path:
    return Path.home() / SETTINGS_FILENAME


def load_settings() -> dict:
    path = settings_path()
    settings = deepcopy(DEFAULT_SETTINGS)
    if not path.is_file():
        return settings

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if isinstance(data.get("ui_theme"), str):
        settings["ui_theme"] = data["ui_theme"]

    if isinstance(data.get("plot_settings"), dict):
        settings["plot_settings"] = {
            **GRAPH_SETTINGS_DEFAULTS,
            **data["plot_settings"],
        }

    if isinstance(data.get("download_settings"), dict):
        settings["download_settings"] = {
            **DEFAULT_DOWNLOAD_SETTINGS,
            **data["download_settings"],
        }

    return settings


def save_settings(settings: dict) -> None:
    path = settings_path()
    payload = {
        "ui_theme": settings.get("ui_theme", DEFAULT_SETTINGS["ui_theme"]),
        "plot_settings": {
            **GRAPH_SETTINGS_DEFAULTS,
            **(settings.get("plot_settings") or {}),
        },
        "download_settings": {
            **DEFAULT_DOWNLOAD_SETTINGS,
            **(settings.get("download_settings") or {}),
        },
    }
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, default_flow_style=False, sort_keys=False)
