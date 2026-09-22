"""Check built distributions before publishing (run with Python 3.11+)."""

import tarfile
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

import tomllib

project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
(wheel,) = Path("dist").glob("*.whl")
(sdist,) = Path("dist").glob("*.tar.gz")

with ZipFile(wheel) as archive:
    names = archive.namelist()
    assert "sentry_structlog/py.typed" in names
    assert not any(name.startswith(("test/", "tests/")) for name in names)
    (metadata_name,) = (name for name in names if name.endswith(".dist-info/METADATA"))
    wheel_metadata = BytesParser().parsebytes(archive.read(metadata_name))

with tarfile.open(sdist) as archive:
    prefix = sdist.name.removesuffix(".tar.gz")
    assert f"{prefix}/sentry_structlog/py.typed" in archive.getnames()
    metadata_file = archive.extractfile(f"{prefix}/PKG-INFO")
    assert metadata_file is not None
    sdist_metadata = BytesParser().parsebytes(metadata_file.read())

for metadata in (wheel_metadata, sdist_metadata):
    assert metadata["Name"] == project["name"]
    assert metadata["Version"] == project["version"]
    assert metadata["Requires-Python"] == ">=3.10"
    classifiers = metadata.get_all("Classifier", [])
    for minor in range(10, 15):
        assert f"Programming Language :: Python :: 3.{minor}" in classifiers
    assert "Typing :: Typed" in classifiers

print("Wheel and sdist metadata, py.typed, and wheel package contents passed.")
