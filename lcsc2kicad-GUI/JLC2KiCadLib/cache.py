import json
import logging
import os


class Cache:
    def __init__(self, output_dir):
        self.output_dir = os.path.abspath(output_dir)
        self.cache_file = os.path.join(self.output_dir, ".jlc2kicad_cache.json")
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Could not load cache: {e}")
        return {"step_hashes": {}, "footprint_hashes": {}, "wrl_hashes": {}}

    def save(self):
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            logging.warning(f"Could not save cache: {e}")

    def get_step_path(self, hash_val):
        path = self.data["step_hashes"].get(hash_val)
        if path:
            return os.path.join(self.output_dir, path)
        return None

    def add_step_path(self, hash_val, path):
        # Store path relative to output_dir
        rel_path = os.path.relpath(path, self.output_dir)
        self.data["step_hashes"][hash_val] = rel_path
        self.save()

    def get_wrl_path(self, hash_val):
        path = self.data.get("wrl_hashes", {}).get(hash_val)
        if path:
            return os.path.join(self.output_dir, path)
        return None

    def add_wrl_path(self, hash_val, path):
        rel_path = os.path.relpath(path, self.output_dir)
        if "wrl_hashes" not in self.data:
            self.data["wrl_hashes"] = {}
        self.data["wrl_hashes"][hash_val] = rel_path
        self.save()

    def get_footprint_name(self, hash_val):
        return self.data["footprint_hashes"].get(hash_val)

    def add_footprint_name(self, hash_val, name):
        self.data["footprint_hashes"][hash_val] = name
        self.save()
