import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class PythonLauncherTests(unittest.TestCase):
    def run_launcher(self, available, configured=None):
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
process.argv = [process.argv[0], "aide-install", "target", "--apply"];
try { require(process.env.LAUNCHER); } catch (e) { if (e.message !== "exit") throw e; }
'''
        env = dict(os.environ, AVAILABLE=available, LAUNCHER=str(launcher))
        env.pop("PYTHON", None)
        if configured is not None:
            env["PYTHON"] = configured
        result = subprocess.run(["node", "-e", script], env=env, text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_fallbacks_and_explicit_python(self):
        for available, expected in (("python", ["python3", "python"]), ("py", ["python3", "python", "py"])):
            with self.subTest(available=available):
                result = self.run_launcher(available)
                self.assertEqual(result["code"], 0)
                self.assertEqual([call[0] for call in result["calls"]], expected)
                self.assertEqual(result["calls"][-1][1], ["target", "--apply"])
        result = self.run_launcher("custom-python", "custom-python")
        self.assertEqual([call[0] for call in result["calls"]], ["custom-python"])
        self.assertEqual(result["code"], 0)
        result = self.run_launcher("python", "missing-python")
        self.assertEqual(result["code"], 127)
        self.assertEqual([call[0] for call in result["calls"]], ["missing-python"])
