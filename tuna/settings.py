"""TUNA config persistence"""
import json
from tuna.config import CONFIG_FILE, DEFAULT_CONFIG


class ConfigManager:
    def __init__(self):
        self._data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    stored = json.load(f)
                self._data.update(stored)
            except Exception:
                pass

    def save(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self.set(key, value)
