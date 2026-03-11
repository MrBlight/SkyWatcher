#!/usr/bin/env python3
"""
SkyWatcher launcher -- handles dependencies and starts the app.
Run this file to start SkyWatcher.
"""
import sys
import subprocess
import importlib

REQUIRED = ["requests"]

def check_deps():
    missing = []
    for pkg in REQUIRED:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Installing missing packages: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)

if __name__ == "__main__":
    check_deps()
    from skywatcher import run
    run()
