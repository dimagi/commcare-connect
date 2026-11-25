import io
import threading
from contextlib import contextmanager
from typing import NamedTuple

from commcare_connect.commcarehq.management.commands.run_fake_hq import FakeHQServer


class RunningFakeHQ(NamedTuple):
    url: str
    output: io.StringIO


@contextmanager
def serve_fake_hq(*, fail_case_updates=False):
    """Run the fake HQ on a free port in a background thread, for the length of the block."""
    output = io.StringIO()
    server = FakeHQServer(port=0, fail_case_updates=fail_case_updates, output=output)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield RunningFakeHQ(url=f"http://127.0.0.1:{server.server_port}", output=output)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
