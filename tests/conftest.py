import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["CONFIG_DIR"] = tempfile.mkdtemp(prefix="dui-config-")
os.environ["BACKUP_DIR"] = tempfile.mkdtemp(prefix="dui-backups-")
os.environ["BROWSE_DIR"] = tempfile.mkdtemp(prefix="dui-browse-")
os.environ["WEB_PASSWORD"] = "test-pass"
