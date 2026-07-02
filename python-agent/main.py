from pathlib import Path
import sys


# gen/ 下是 protoc 生成的代码,加进 sys.path 才能 import api.provider.v1
ROOT = Path(__file__).resolve().parent
GEN_DIR = ROOT / "gen"
if str(GEN_DIR) not in sys.path:
    sys.path.insert(0, str(GEN_DIR))


from app.config import load_settings
from app.logger import setup_logging
from app.server import serve


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    serve(settings)


if __name__ == "__main__":
    main()
