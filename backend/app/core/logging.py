import logging


def configure_logging(log_level: str) -> None:
    """Configure standard library logging for the application."""
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
