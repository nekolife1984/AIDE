#!/usr/bin/env node

const path = require("node:path");
const { spawnSync } = require("node:child_process");

const installer = path.resolve(__dirname, "../scripts/aide_install.py");
const candidates = process.env.PYTHON ? [process.env.PYTHON] : ["python3", "python", "py"];
for (const python of candidates) {
  const result = spawnSync(python, [installer, ...process.argv.slice(2)], {
    stdio: "inherit",
  });
  if (result.error && result.error.code === "ENOENT" && !process.env.PYTHON) continue;
  if (result.error) {
    console.error(`aide-install: ${python} を起動できません: ${result.error.message}`);
    process.exit(127);
  }
  process.exit(result.status === null ? 1 : result.status);
}
console.error("aide-install: Python が見つかりません。PYTHON に実行ファイルを指定してください。");
process.exit(127);
