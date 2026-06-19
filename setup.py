from setuptools import setup, find_packages
from pathlib import Path

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding="utf-8")

setup(
    name="autocheckpoint",
    version="0.3.7",
    author="AutoCheckpoint Maintainers",
    author_email="maintainers@autocheckpoint.dev",
    description="Never lose your code or your project context again. AI Development Continuity tool.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/epicadidash/talvo-demo",  # From user workspace name
    packages=find_packages(),
    install_requires=[
        "watchfiles>=0.21.0",
        "typer>=0.9.0",
        "rich>=13.7.0",
        "pyyaml>=6.0.1",
        "pathspec>=0.12.1",
    ],
    entry_points={
        "console_scripts": [
            "autocheckpoint=autocheckpoint.cli:app",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
)
