"""The running web_api script must be the only web_api module.

entrypoint.sh starts ``python3 apps/api/web_api.py``, so the server runs as
``__main__``. Before the fix, the first ``from apps.api import web_api`` (the
Teamarr preflight service's automation-status lookup does one) executed a
second copy with its own globals. That copy's get_automation_manager() built a
second, idle AutomatedStreamManager whose ``period_last_run`` never moved, and
the upcoming-events schedule read it: the dashboard and the widget showed a
frozen run grid instead of the real next run.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

PROBE = textwrap.dedent(
    """
    import runpy, sys
    sys.argv = ["web_api.py", "--help"]   # argparse exits before the server starts
    try:
        runpy.run_path("apps/api/web_api.py", run_name="__main__")
    except SystemExit:
        pass
    running = sys.modules.get("apps.api.web_api")

    from apps.api import web_api
    import web_api as legacy_alias
    from apps.automation.automation_events_scheduler import AutomationEventsScheduler

    manager = web_api.get_automation_manager()
    # The scheduler finds the manager through sys.modules; it must be this one.
    found = sys.modules["apps.api.web_api"].get_automation_manager()
    print("RESULT",
          running is not None and web_api is running,
          legacy_alias is web_api,
          web_api.__name__,
          found is manager)
    """
)


def test_script_run_is_reused_by_package_imports(tmp_path):
    env = dict(os.environ, PYTHONPATH=".", CONFIG_DIR=str(tmp_path))
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith("RESULT")]
    assert lines, completed.stdout[-2000:] + completed.stderr[-2000:]
    _, same_module, legacy_same, name, same_manager = lines[-1].split()
    assert same_module == "True", "from apps.api import web_api executed a second copy"
    assert legacy_same == "True"
    assert name == "__main__"
    assert same_manager == "True"
