import { constants } from "node:fs";
import { open, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";

export function validateRequest(request, config) {
  if (!request || typeof request !== "object" || Array.isArray(request) || Object.keys(request).some(key => !["action", "task", "repositories", "brief"].includes(key))) throw new Error("Invalid handoff request fields");
  if (!["create", "add"].includes(request.action)) throw new Error("Action must be create or add");
  if (typeof request.task !== "string" || request.task.length > 64 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(request.task)) throw new Error("Task must be lowercase kebab-case, at most 64 characters");
  if (request.action === "add" && request.task !== config.currentTask) throw new Error("Add is only allowed for the current named task");
  if (typeof request.brief !== "string" || !request.brief.trim() || request.brief.length > 64000 || /[\u0000-\u0008\u000b-\u001f\u007f\u202a-\u202e\u2066-\u2069]/.test(request.brief)) throw new Error("Brief must be nonempty Markdown under 64,000 characters without terminal control characters");
  if (!Array.isArray(request.repositories) || request.repositories.length < 1 || request.repositories.length > 8) throw new Error("Choose one to eight repositories");
  const repositories = request.repositories.map(value => {
    if (typeof value !== "string") throw new Error("Repository must be a name or path");
    const matches = config.repositories.filter(path => path === value || basename(path) === value);
    if (matches.length !== 1) throw new Error(`Unknown or ambiguous repository: ${value}`);
    if (request.action === "add" && config.currentRepositories.includes(matches[0])) throw new Error(`Repository already belongs to this task: ${value}`);
    return matches[0];
  });
  if (new Set(repositories).size !== repositories.length) throw new Error("Duplicate repository");
  return { action: request.action, task: request.task, repositories, brief: request.brief };
}

async function readRequest(path) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > 300000) throw new Error("Handoff request must be a regular JSON file under 300 KB");
    return JSON.parse(await file.readFile("utf8"));
  } finally {
    await file.close();
  }
}

export function reviewText(request) {
  return [
    `${request.action === "create" ? "Create" : "Expand"} task: ${request.task}`,
    "",
    "New writable worktrees:",
    ...request.repositories.map(path => join(path, ".pi/worktrees", request.task)),
    "",
    "Original checkouts and PARA remain read-only. No publication or commits are authorized.",
    "Approval ends this Pi session and starts/resumes the task in this terminal.",
    "",
    "Brief preview (the full prepared brief is carried into the task):",
    request.brief.slice(0, 1200) + (request.brief.length > 1200 ? "\n[Preview truncated; review the full request before confirming if needed.]" : ""),
  ].join("\n");
}

export default function projectHandoff(pi) {
  let pending = false;
  pi.registerCommand("project-handoff", {
    description: "Review the prepared handoff and approve task worktrees",
    handler: async (args, ctx) => {
      if (pending) return;
      if (args.trim() || ctx.mode !== "tui") {
        ctx.ui.notify("Use /project-handoff without arguments in interactive pi-task.", "error");
        return;
      }
      pending = true;
      try {
        await ctx.waitForIdle();
        const configPath = process.env.PI_TASK_HANDOFF_CONFIG;
        if (!configPath) throw new Error("This command requires the pi-task launcher");
        const config = JSON.parse(await readFile(configPath, "utf8"));
        const request = validateRequest(await readRequest(config.requestPath), config);
        const approved = await ctx.ui.confirm("Approve project handoff?", reviewText(request));
        if (!approved) {
          ctx.ui.notify("Handoff cancelled; no worktrees created.", "info");
          return;
        }
        await writeFile(config.approvalPath, JSON.stringify(request), { flag: "wx", mode: 0o600 });
        ctx.ui.notify("Approved. Switching to the task through the host launcher.", "info");
        ctx.shutdown();
      } catch (error) {
        ctx.ui.notify(error.code === "ENOENT" ? "No handoff request yet. Ask the agent to prepare a project handoff first." : error.message, "error");
      } finally {
        pending = false;
      }
    },
  });
}
