from setuptools import setup, find_packages
from pathlib import Path

long_desc = (Path(__file__).parent / "README.md").read_text(encoding="utf-8")

setup(
    name="tuna",
    version="1.1.0",
    description="TUNA — Terminal music player with real-time audio visualizer",
    long_description=long_desc,
    long_description_content_type="text/markdown",
    author="TUNA Project",
    author_email="",
    url="https://github.com/randometerian/TUNA.git",
    license="MIT",
    python_requires=">=3.11",
    packages=find_packages(),
    install_requires=[
        "mutagen>=1.47",
        "Pillow>=10.0",
        "pyaudio>=0.2.14",
        "numpy>=1.26",
    ],
    extras_require={
        "dev": [],
    },
    entry_points={
        "console_scripts": [
            "tuna=tuna.__main__:main",
        ],
    },
    classifiers=[
        "Environment :: Console :: Curses",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Sound/Audio :: Players",
    ],
    keywords="music player terminal audio visualizer curses",
)