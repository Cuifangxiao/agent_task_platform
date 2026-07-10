"""进程入口。统一从 python-agent 根目录启动:

    python main.py --conf config.yaml
"""

import argparse

from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(description="python agent runtime")
    parser.add_argument("--conf", default="config.yaml", help="path to config yaml")
    args = parser.parse_args()

    load_dotenv()

    from config import init_config

    config = init_config(args.conf)

    from utils.logger import configure_logger

    configure_logger(config.log)

    from app.application import Application

    Application(config).run()


if __name__ == "__main__":
    main()
