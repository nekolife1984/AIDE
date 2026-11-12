import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class PythonLauncherTests(unittest.TestCase):
    def run_launcher(self, available, configured=None, platform="linux"):
        launcher = Path(__file__).resolve().parents[2] / "bin/aide-install.js"
        script = r'''
const Module = require("module");
const original = Module._load;
const calls = [];
Module._load = function(name, ...args) {
  if (name === "node:child_process") return {spawnSync: (cmd, argv) => {
    calls.push([cmd, argv.slice(1)]);
    if (cmd !== process.env.AVAILABLE) return {error: {code: "ENOENT", message: "missing"}};
    return {status: 0};
  }};
  return original.call(this, name, ...args);
};
process.exit = code => { console.log(JSON.stringify({code, calls})); throw new Error("exit"); };
Object.defineProperty(process, "platform", {value: process.env.TEST_PLATFORM});
process.argv = [process.argv[0], "aide-install", "target", "--apply"];
try { require(process.env.LAUNCHER); } catch (e) { if (e.message !== "exit") throw e; }
'''
        env = dict(os.environ, AVAILABLE=available, LAUNCHER=str(launcher), TEST_PLATFORM=platform)
        env.pop("PYTHON", None)
        if configured is not None:
            env["PYTHON"] = configured
        result = subprocess.run(["node", "-e", script], env=env, text=True, capture_output=True, check=True)
        return json.loads(result.stdout), result.stderr

    def test_fallbacks_and_explicit_python(self):
        for available, expected in (("python", ["python3", "python"]), ("py", ["python3", "python", "py"])):
            with self.subTest(available=available):
                result, _ = self.run_launcher(available)
                self.assertEqual(result["code"], 0)
                self.assertEqual([call[0] for call in result["calls"]], expected)
                self.assertEqual(result["calls"][-1][1], ["target", "--apply"])
        result, _ = self.run_launcher("custom-python", "custom-python")
        self.assertEqual([call[0] for call in result["calls"]], ["custom-python"])
        self.assertEqual(result["code"], 0)
        result, _ = self.run_launcher("python", "missing-python")
        self.assertEqual(result["code"], 127)
        self.assertEqual([call[0] for call in result["calls"]], ["missing-python"])

    def test_windows_prefers_py_and_python_over_store_stub(self):
        for available, expected in (("py", ["py"]), ("python", ["py", "python"]),
                                    ("python3", ["py", "python", "python3"])):
            with self.subTest(available=available):
                result, _ = self.run_launcher(available, platform="win32")
                self.assertEqual(result["code"], 0)
                self.assertEqual([call[0] for call in result["calls"]], expected)
                self.assertEqual(result["calls"][-1][1], ["target", "--apply"])
        result, _ = self.run_launcher("custom-python", "custom-python", platform="win32")
        self.assertEqual(result["code"], 0)
        self.assertEqual([call[0] for call in result["calls"]], ["custom-python"])

    def test_all_candidates_missing(self):
        for platform, expected in (("linux", ["python3", "python", "py"]),
                                   ("win32", ["py", "python", "python3"])):
            with self.subTest(platform=platform):
                result, stderr = self.run_launcher("none", platform=platform)
                self.assertEqual(result["code"], 127)
                self.assertEqual([call[0] for call in result["calls"]], expected)
                self.assertIn("Python が見つかりません", stderr)
