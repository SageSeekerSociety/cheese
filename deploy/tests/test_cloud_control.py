import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "cloud_control", Path(__file__).resolve().parents[1] / "cloud-control.py"
)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_keeps_connection_and_release_closes_it(self):
        tasks, events = {}, []
        key = (1, "device1", "192.0.2.1", "cheese")

        async def worker(key):
            events.append(("open", key))
            try:
                await asyncio.Future()
            finally:
                events.append(("close", key))

        await control.reconcile(tasks, {key}, worker)
        await asyncio.sleep(0)
        await control.reconcile(tasks, {key}, worker)
        await asyncio.sleep(0)
        self.assertEqual(events, [("open", key)])
        await control.reconcile(tasks, {}, worker)
        self.assertEqual(events, [("open", key), ("close", key)])
        self.assertEqual(tasks, {})

    async def test_recycled_ip_closes_previous_identity_before_connecting(self):
        tasks, events = {}, []
        old = (1, "device1", "192.0.2.1", "cheese")
        new = (2, "device2", "192.0.2.1", "cheese")

        async def worker(key):
            events.append(("open", key))
            try:
                await asyncio.Future()
            finally:
                events.append(("close", key))

        await control.reconcile(tasks, {old}, worker)
        await asyncio.sleep(0)
        await control.reconcile(tasks, {new}, worker)
        await asyncio.sleep(0)
        self.assertEqual(events, [("open", old), ("close", old), ("open", new)])
        await control.reconcile(tasks, {}, worker)

    async def test_stopping_worker_reaps_its_actual_child_process(self):
        spawn = asyncio.create_subprocess_exec
        children = []
        started = asyncio.Event()

        async def local_child(*args, **kwargs):
            process = await spawn(sys.executable, "-c", "import time; time.sleep(60)",
                                  **kwargs)
            children.append(process)
            started.set()
            return process

        with TemporaryDirectory() as directory:
            with patch.object(control.asyncio, "create_subprocess_exec", local_child):
                task = asyncio.create_task(control.forward(
                    (1, "device1", "192.0.2.1", "cheese"), Path(directory), 8081
                ))
                await asyncio.wait_for(started.wait(), 3)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIsNotNone(children[0].returncode)


if __name__ == "__main__":
    unittest.main()
