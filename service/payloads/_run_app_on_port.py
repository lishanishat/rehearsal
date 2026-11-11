#!/usr/bin/env python3
"""
_run_app_on_port.py — test-only launcher for app.py on an alternate port.

app.py hardcodes `app.run(host="0.0.0.0", port=8080)` in its
`if __name__ == "__main__":` block, and we were asked not to edit app.py.
test_locally.sh needs to run its own throwaway instance without colliding
with port 8080 if something else already happens to be bound to it on the
test machine, so this tiny shim monkeypatches Flask.run() to force the port
given on argv before executing app.py's own __main__ block via runpy. It
does not change app.py on disk in any way.

Usage: python3 _run_app_on_port.py <service_dir> <port>
"""
import runpy
import sys

import flask

service_dir, port = sys.argv[1], int(sys.argv[2])

_orig_run = flask.Flask.run


def _run_on_fixed_port(self, *args, **kwargs):
    kwargs["port"] = port
    return _orig_run(self, *args, **kwargs)


flask.Flask.run = _run_on_fixed_port

sys.path.insert(0, service_dir)
runpy.run_path(service_dir + "/app.py", run_name="__main__")
