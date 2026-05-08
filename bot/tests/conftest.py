import sys
import os

# Allow tests to import bot/ modules by bare name (e.g. from market_data import ...)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
