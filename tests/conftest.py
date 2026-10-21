import os

os.environ.setdefault("TMPDIR", os.path.join(os.path.dirname(__file__), "..", ".pytest_tmp"))
os.environ.setdefault("TEMP", os.environ["TMPDIR"])
os.environ.setdefault("TMP", os.environ["TMPDIR"])