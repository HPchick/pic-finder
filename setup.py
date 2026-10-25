"""Build wheel resources from the single maintained dictionary source."""
from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py

DICTIONARY_FILES = ("manifest.json", "common.json", "pose.json", "outfit.json", "prop.json", "scene.json")
ZH_FILES = ("artist.csv", "character.csv", "copyright.csv", "general.csv", "meta.csv")

class BuildWithDictionary(build_py):
    def run(self):
        super().run()
        source = Path(__file__).resolve().parent / "work" / "dictionary"
        destination = Path(self.build_lib) / "picfinder" / "resources" / "dictionary"
        for relative in [*DICTIONARY_FILES, *("zh/" + name for name in ZH_FILES)]:
            src, dst = source / relative, destination / relative
            if not src.is_file():
                raise FileNotFoundError(f"Missing bundled dictionary: {src}")
            self.mkpath(str(dst.parent))
            self.copy_file(str(src), str(dst))

setup(cmdclass={"build_py": BuildWithDictionary})
