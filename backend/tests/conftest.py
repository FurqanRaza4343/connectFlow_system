"""Unit tests need no database, Redis or network.  Settings that are required at import time get dummy values."""
import os
import pathlib
import sys

os.environ.setdefault("JWT_SECRET", "unit-test-secret-not-used-anywhere-real")
os.environ.setdefault("OPENWA_WEBHOOK_SECRET", "unit-test-webhook-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost:1/x")  # never connected to
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
