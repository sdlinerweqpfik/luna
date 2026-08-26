import os
import yaml

def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # раскрываем ~ в путях
    if "voice" in cfg.get("piper", {}):
        cfg["piper"]["voice"] = os.path.expanduser(cfg["piper"]["voice"])
    return cfg
