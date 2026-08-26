import logging

def setup_logging(cfg):
    logging.basicConfig(
        filename=cfg.get("file", "secretary.log"),
        level=getattr(logging, cfg.get("level", "INFO")),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        encoding="utf-8")
