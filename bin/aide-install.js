#!/usr/bin/env node

const path = require("node:path");
const { spawnSync } = require("node:child_process");

const installer = path.resolve(__dirname, "../scripts/aide_install.py");
const python = process.env.PYTHON || "python3";
const result = spawnSync(python, [installer, ...process.argv.slice(2)], {
  stdio: "inherit",
});

if (result.error) {
  console.error(`aide-install: ${python} を起動できません: ${result.error.message}`);
  process.exit(127);
}

process.exit(result.status === null ? 1 : result.status);
