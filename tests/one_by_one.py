"""Runs each test of some files in a process of its own.

    python tests/one_by_one.py tests/test_window.py

On the Mac, Tk hangs after some windows have been opened and closed in the same process. The
program opens only one.
"""

import subprocess
import sys

LIMIT = 60  # seconds

listed = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *sys.argv[1:]],
                        capture_output=True, text=True, check=True).stdout
bad = []
for test in (line for line in listed.splitlines() if "::" in line):
    try:
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                              "-o", f"faulthandler_timeout={LIMIT - 10}", test],
                             capture_output=True, text=True, timeout=LIMIT)
        good, text = run.returncode == 0, run.stdout
    except subprocess.TimeoutExpired as e:
        good, text = False, f"{e.stdout}\n{e.stderr}\nmore than {LIMIT} s"
    print("ok  " if good else "BAD ", test, flush=True)
    if not good:
        bad.append(test)
        print(text)
print(f"{len(bad)} bad" if bad else "all good")
sys.exit(bool(bad))
