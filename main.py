import logging

from scalper.bot import ScalpingBot
from scalper.config import Config

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ScalpingBot(Config()).run()
