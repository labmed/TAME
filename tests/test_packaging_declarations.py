"""What the build and start scripts declare agrees with what the program needs when it runs."""
from pathlib import Path
import re

import pytest

from tametools.toml_compat import loads

ROOT = Path(__file__).resolve().parents[1]


def test_the_windows_build_packages_the_web_examples():
    main = (ROOT / "web/backend/app/main.py").read_text(encoding="utf-8")
    spec = (ROOT / "packaging/windows/tametools.spec").read_text(encoding="utf-8")
    assert "parents[1]/'examples'" in main                               # where the running app looks, next to the app package
    assert '(str(BACKEND / "examples"), "examples")' in spec             # the same place inside the packaged app
    served = re.findall(r"\('(\w+\.tame)',", main)
    assert served and all((ROOT / "web/backend/examples" / name).is_file() for name in served)


def test_the_offline_bundle_prepares_installs_and_starts_with_one_list_of_extras():
    names = ("build_windows_offline_bundle.bat", "install_windows_offline_bundle.bat", "start_tametools_web.bat")
    paths = [ROOT / "scripts" / name for name in names]
    if not all(path.is_file() for path in paths):
        pytest.skip("the offline bundle scripts are not part of this copy")
    texts = [path.read_text(encoding="utf-8") for path in paths]
    defaults = [re.search(r'set "TAMETOOLS_WEB_EXTRAS=([\w,]+)"', text).group(1) for text in texts]
    assert len(set(defaults)) == 1, dict(zip(names, defaults))
    declared = set(loads((ROOT / "tametools/pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"])
    assert set(defaults[0].split(",")) <= declared
    assert all("tametools[report,web]" not in text for text in texts)     # no second, shorter list anywhere
    assert "extras.txt" in texts[0] and all("extras.txt" in text for text in texts[1:])   # the bundle carries its own list
