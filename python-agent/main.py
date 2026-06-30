from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
GEN_DIR = ROOT / "gen"
if str(GEN_DIR) not in sys.path:
    sys.path.insert(0, str(GEN_DIR))


from app.server import serve


if __name__ == "__main__":
    serve()
