"""Guard against relying on packages that only arrive as someone else's dependency.

certifi was once assumed present because it happened to be installed transitively in the
development environment; on a user's Mac it was not, and the TLS fallback had nothing to use.
"""

import re
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _core_dependencies() -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.DOTALL | re.MULTILINE)
    assert block, "could not find the dependencies list in pyproject.toml"
    return re.findall(r'^\s*"([^"]+)"', block.group(1), re.MULTILINE)


def test_certifi_is_a_direct_dependency():
    names = [re.split(r"[<>=!~ ]", dep, maxsplit=1)[0].lower() for dep in _core_dependencies()]
    assert "certifi" in names


def test_every_third_party_module_clipper_imports_is_declared():
    # Imports inside the package that are not stdlib or clipper itself.
    declared = {
        re.split(r"[<>=!~ ]", dep, maxsplit=1)[0].lower().replace("-", "_")
        for dep in _core_dependencies()
    }
    # import name -> distribution name
    import_to_dist = {
        "cv2": "opencv_contrib_python",
        "PIL": "pillow",
        "dotenv": "python_dotenv",
        "yt_dlp": "yt_dlp",
        "openai": "openai",
        "numpy": "numpy",
        "certifi": "certifi",
    }
    source = "\n".join(p.read_text(encoding="utf-8") for p in (PYPROJECT.parent / "clipper").glob("*.py"))
    used = {name for name in import_to_dist if re.search(rf"^\s*(import|from)\s+{name}\b", source, re.MULTILINE)}
    missing = sorted(import_to_dist[name] for name in used if import_to_dist[name] not in declared)
    assert not missing, f"imported but not declared in pyproject.toml: {missing}"
