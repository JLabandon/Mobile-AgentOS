from subprocess import CompletedProcess

from agent_ipc_mvp.adb import AdbClient


class FakeAdb(AdbClient):
    def run(self, *args, check=False, timeout=30):
        return CompletedProcess(
            args=list(args),
            returncode=0,
            stdout=(
                "List of devices attached\n"
                "emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64\n"
            ),
            stderr="",
        )


def test_require_device_accepts_space_aligned_adb_output() -> None:
    adb = FakeAdb(device="emulator-5554")
    assert adb.require_device() == "emulator-5554"
