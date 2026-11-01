import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import projectHandoff, { validateRequest } from "./project-handoff.mjs";

const request = { action: "create", task: "my-talk", repositories: ["presentations"], brief: "# Task\nCreate a talk outline." };
const config = { repositories: ["/projects/presentations", "/projects/website"], currentTask: "existing", currentRepositories: ["/projects/presentations"] };

test("resolves eligible repos and rejects unsafe requests", () => {
  assert.deepEqual(validateRequest(request, config).repositories, ["/projects/presentations"]);
  for (const bad of [
    { ...request, repositories: ["/Users"] },
    { ...request, repositories: ["presentations", "/projects/presentations"] },
    { ...request, task: "../escape" },
    { ...request, action: "add", task: "another-task" },
    { ...request, action: "add", task: "existing" },
    { ...request, command: "host-shell" },
    { ...request, brief: "\u001b[2J" },
  ]) assert.throws(() => validateRequest(bad, config));
  assert.equal(validateRequest({ ...request, action: "add", task: "existing", repositories: ["website"] }, config).action, "add");
});

async function commandFixture(callback) {
  const root = await mkdtemp(join(tmpdir(), "handoff-command-test-"));
  const previous = process.env.PI_TASK_HANDOFF_CONFIG;
  try {
    const paths = { ...config, requestPath: join(root, "request.json"), approvalPath: join(root, "approved.json") };
    const configPath = join(root, "config.json");
    await writeFile(configPath, JSON.stringify(paths));
    await writeFile(paths.requestPath, JSON.stringify(request));
    process.env.PI_TASK_HANDOFF_CONFIG = configPath;
    let command;
    projectHandoff({ registerCommand: (name, definition) => { assert.equal(name, "project-handoff"); command = definition; } });
    let shutdowns = 0;
    const ctx = { mode: "tui", waitForIdle: async () => {}, shutdown: () => { shutdowns++; }, ui: { notify: () => {}, confirm: async () => false } };
    await callback({ paths, command, ctx, shutdowns: () => shutdowns });
  } finally {
    if (previous === undefined) delete process.env.PI_TASK_HANDOFF_CONFIG;
    else process.env.PI_TASK_HANDOFF_CONFIG = previous;
    await rm(root, { recursive: true, force: true });
  }
}

test("cancellation creates no approval and keeps session", async () => {
  await commandFixture(async ({ command, ctx, paths, shutdowns }) => {
    await command.handler("", ctx);
    await assert.rejects(readFile(paths.approvalPath), { code: "ENOENT" });
    assert.equal(shutdowns(), 0);
  });
});

test("approval freezes the reviewed proposal", async () => {
  await commandFixture(async ({ command, ctx, paths, shutdowns }) => {
    ctx.ui.confirm = async (title, text) => {
      assert.match(text, /\/projects\/presentations\/\.pi\/worktrees\/my-talk/);
      await writeFile(paths.requestPath, JSON.stringify({ ...request, repositories: ["website"], brief: "changed after review" }));
      return true;
    };
    await command.handler("", ctx);
    const approved = JSON.parse(await readFile(paths.approvalPath));
    assert.deepEqual(approved.repositories, ["/projects/presentations"]);
    assert.equal(approved.brief, request.brief);
    assert.equal(shutdowns(), 1);
  });
});

test("headless calls and symlinks cannot approve", async () => {
  await commandFixture(async ({ command, ctx, paths, shutdowns }) => {
    ctx.ui.confirm = async () => assert.fail("Approval UI should not be reached");
    ctx.mode = "rpc";
    await command.handler("", ctx);
    ctx.mode = "tui";
    await rm(paths.requestPath);
    await symlink("config.json", paths.requestPath);
    await command.handler("", ctx);
    assert.equal(shutdowns(), 0);
  });
});
