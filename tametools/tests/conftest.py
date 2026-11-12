"""Tests read what tametools prints as UTF-8, whatever this user has chosen for piped text (tametools doctor --pipe-text)."""
import os

os.environ["TAMETOOLS_PIPE_TEXT"] = "utf-8"
