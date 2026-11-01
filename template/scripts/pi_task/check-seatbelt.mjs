import assert from "node:assert/strict";
import { cp, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const extension = process.argv[2];
const root = await mkdtemp(join(tmpdir(), "pi-seatbelt-check-"));
let profile;
try {
  await cp(join(extension, "src"), join(root, "src"), { recursive: true });
  const { createProfileFile } = await import(pathToFileURL(join(root, "src/seatbelt.ts")));
  const { buildPolicy, assertCanWrite } = await import(pathToFileURL(join(root, "src/policy.ts")));
  const { registerToolGuard } = await import(pathToFileURL(join(root, "src/tool-guard.ts")));
  const workspace = join(root, "workspace");
  const secondary = join(root, "website-worktree");
  const para = join(root, "para");
  const outside = join(root, "outside");
  for (const directory of [workspace, secondary, para, outside]) await mkdir(directory);
  const note = join(para, "note.md");
  await writeFile(note, "human-authored fixture");
  await symlink(para, join(workspace, "notes-link"));
  const config = { readable: [root, "/bin", "/usr", "/System", "/Library", "/dev/null"], writable: [workspace, secondary], denyRead: [], denyWrite: [para, outside], network: "none" };
  profile = await createProfileFile(config);
  const shell = command => spawnSync("/usr/bin/sandbox-exec", ["-f", profile.path, "/bin/bash", "-c", command], { encoding: "utf8" });
  assert.equal(shell(`cat '${note}'`).status, 0, "shell can read PARA fixture");
  assert.equal(shell(`echo allowed > '${workspace}/draft.md'`).status, 0, "shell can write workspace");
  assert.equal(shell(`cd '${secondary}' && echo allowed > page.md`).status, 0, "shell can write secondary task worktree");
  for (const command of [
    `echo forbidden > '${note}'`,
    `rm '${note}'`,
    `mv '${note}' '${workspace}/moved.md'`,
    `echo forbidden > '${workspace}/notes-link/new.md'`,
    `echo forbidden > '${outside}/new.md'`,
  ]) assert.notEqual(shell(command).status, 0, command);
  assert.equal(await readFile(note, "utf8"), "human-authored fixture");
  const policy = buildPolicy(config, workspace);
  assertCanWrite(join(workspace, "new.md"), policy);
  assertCanWrite(join(secondary, "new.md"), policy);
  assert.throws(() => assertCanWrite(note, policy));
  assert.throws(() => assertCanWrite(join(workspace, "notes-link/new.md"), policy));
  let guard;
  registerToolGuard({ on: (name, handler) => { assert.equal(name, "tool_call"); guard = handler; } }, { isActive: () => true, getPolicy: () => policy });
  for (const toolName of ["write", "edit"]) assert.equal(guard({ toolName, input: { path: note } }).block, true);
  assert.equal(guard({ toolName: "read", input: { path: note } }), undefined);
  assert.equal(guard({ toolName: "write", input: {} }).block, true);
  console.log("PASS: native multi-root shell/file writes; denied PARA overwrite/delete/move/symlink/outside writes");
} finally {
  if (profile) await profile.dispose();
  await rm(root, { recursive: true, force: true });
}
