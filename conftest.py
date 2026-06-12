import sys
from pathlib import Path


def pytest_configure(config):
    root = str(Path(__file__).parent)
    if root not in sys.path:
        sys.path.insert(0, root)
