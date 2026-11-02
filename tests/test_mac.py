import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS backend")

from slow_coffee import mac

PMSET_BATT = """Now drawing from '%s'
 -InternalBattery-0 (id=1)\t%s%%; discharging; 3:10 remaining present: true
"""


def fake_run(stdout):
    return mock.Mock(return_value=mock.Mock(stdout=stdout, returncode=0, stderr=""))


class Pmset(unittest.TestCase):
    def test_battery_on_ac(self):
        with mock.patch.object(mac.subprocess, "run", fake_run(PMSET_BATT % ("AC Power", 87))):
            self.assertEqual(mac.battery(), (87, True))

    def test_battery_on_battery(self):
        with mock.patch.object(mac.subprocess, "run", fake_run(PMSET_BATT % ("Battery Power", 12))):
            self.assertEqual(mac.battery(), (12, False))

    def test_battery_absent(self):
        with mock.patch.object(mac.subprocess, "run", fake_run("Now drawing from 'AC Power'\n")):
            self.assertEqual(mac.battery(), (None, True))

    def test_sleep_disabled(self):
        for flag, want in (("1", True), ("0", False)):
            out = " sleep 1\n SleepDisabled\t\t%s\n" % flag
            with mock.patch.object(mac.subprocess, "run", fake_run(out)):
                self.assertIs(mac.sleep_disabled(), want)

    def test_sleep_disabled_absent_means_off(self):
        with mock.patch.object(mac.subprocess, "run", fake_run(" sleep 1\n")):
            self.assertFalse(mac.sleep_disabled())

    def test_pmset_missing_is_not_fatal(self):
        with mock.patch.object(mac.subprocess, "run", side_effect=OSError):
            self.assertFalse(mac.sleep_disabled())
            self.assertEqual(mac.battery(), (None, True))


class Admin(unittest.TestCase):
    def test_script_is_quoted_into_applescript(self):
        with mock.patch.object(mac.subprocess, "run", fake_run("")) as run:
            self.assertTrue(mac._admin('echo "hi" \\ there'))
        applescript = run.call_args[0][0][2]
        self.assertIn('with administrator privileges', applescript)
        self.assertIn('echo \\"hi\\" \\\\ there', applescript)

    def test_cancel_returns_false(self):
        bad = mock.Mock(return_value=mock.Mock(returncode=1, stderr="User canceled."))
        with mock.patch.object(mac.subprocess, "run", bad):
            self.assertFalse(mac._admin("true"))


class LidMode(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.flag = os.path.join(self.dir, "lid.flag")
        for name, val in (("STATE_DIR", self.dir), ("LID_FLAG", self.flag)):
            p = mock.patch.object(mac, name, val)
            p.start()
            self.addCleanup(p.stop)

    def test_enable_writes_flag_and_runs_admin(self):
        with mock.patch.object(mac, "_admin", return_value=True) as admin:
            self.assertTrue(mac.enable_lid_mode())
        self.assertTrue(os.path.exists(self.flag))
        script = admin.call_args[0][0]
        self.assertIn("disablesleep 1", script)
        self.assertIn(str(os.getpid()), script)

    def test_declined_prompt_leaves_no_flag(self):
        with mock.patch.object(mac, "_admin", return_value=False):
            self.assertFalse(mac.enable_lid_mode())
        self.assertFalse(os.path.exists(self.flag))

    def test_disable_removes_flag_and_tolerates_absence(self):
        open(self.flag, "w").close()
        mac.disable_lid_mode()
        self.assertFalse(os.path.exists(self.flag))
        mac.disable_lid_mode()

    def _run_watcher(self, pid, flag_present):
        """Run the real watcher snippet with `pmset` swapped for a marker file."""
        marker = os.path.join(self.dir, "restored")
        fake = os.path.join(self.dir, "pmset")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho \"$@\" > %s\n" % marker)
        os.chmod(fake, 0o755)
        if flag_present:
            open(self.flag, "w").close()
        cmd = mac.watcher_command(pid, self.flag, pmset=fake, interval=0.2)
        subprocess.run(["/bin/sh", "-c", cmd], check=True)
        for _ in range(30):
            if os.path.exists(marker):
                with open(marker) as f:
                    return f.read().strip()
            time.sleep(0.1)
        return None

    def test_watcher_restores_sleep_when_flag_removed(self):
        self.assertEqual(self._run_watcher(os.getpid(), flag_present=False),
                         "-a disablesleep 0")

    def test_watcher_restores_sleep_when_owner_dies(self):
        owner = subprocess.Popen(["/bin/sleep", "0.3"])
        owner.wait()    # reaped: an unreaped zombie still answers kill -0
        self.assertEqual(self._run_watcher(owner.pid, True), "-a disablesleep 0")

    def test_watcher_holds_while_owner_and_flag_live(self):
        owner = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(owner.wait)
        self.addCleanup(owner.kill)
        self.assertIsNone(self._run_watcher(owner.pid, True))
        # flag removal then releases it
        os.remove(self.flag)
        marker = os.path.join(self.dir, "restored")
        for _ in range(30):
            if os.path.exists(marker):
                break
            time.sleep(0.1)
        self.assertTrue(os.path.exists(marker))


class LaunchAgent(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "LaunchAgents", "x.plist")
        p = mock.patch.object(mac, "LAUNCH_AGENT", self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_install_writes_plist(self):
        with mock.patch.object(mac.subprocess, "run") as run:
            mac.install_launch_agent(start_active=False, load=False)
        run.assert_not_called()
        with open(self.path, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["ProgramArguments"][1:], ["-m", "slow_coffee", "--start-inactive"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})

    def test_install_active_has_no_inactive_flag(self):
        mac.install_launch_agent(start_active=True, load=False)
        with open(self.path, "rb") as f:
            self.assertNotIn("--start-inactive", plistlib.load(f)["ProgramArguments"])

    def test_install_load_bootstraps(self):
        with mock.patch.object(mac.subprocess, "run") as run:
            mac.install_launch_agent(load=True)
        verbs = [c[0][0][1] for c in run.call_args_list]
        self.assertEqual(verbs, ["bootout", "bootstrap"])

    def test_uninstall_round_trip(self):
        mac.install_launch_agent(load=False)
        with mock.patch.object(mac.subprocess, "run"):
            self.assertEqual(mac.uninstall_launch_agent(), [self.path])
            self.assertEqual(mac.uninstall_launch_agent(), [])


class Modes(unittest.TestCase):
    def test_every_mode_has_caffeinate_flags(self):
        for _label, mode in mac.MODES:
            self.assertIn(mode, mac.CAFFEINATE_FLAGS)

    def test_only_idle_holds_the_display(self):
        self.assertIn("-d", mac.CAFFEINATE_FLAGS["idle"])
        self.assertNotIn("-d", mac.CAFFEINATE_FLAGS["lid"])


if __name__ == "__main__":
    unittest.main()
