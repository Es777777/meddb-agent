"""Portable repository entry point; delegates to the single CLI runtime."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from meddb.cli import main
if __name__ == '__main__':
    main()
