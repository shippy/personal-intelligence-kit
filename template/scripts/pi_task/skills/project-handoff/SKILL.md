---
name: project-handoff
description: Prepare an agreed idea or spec for execution in project worktrees, or propose adding repositories to the current named task. Use when the user asks to hand work off, move from exploration to implementation, or involve another project.
---

# Project handoff

Turn the conversation into a reviewable handoff, not a host command.

1. Identify the intended outcome and repositories that actually need edits. Use
   the launch context's eligible repository list. Read-only research does not
   require a new worktree. Resolve material ambiguities with the user; do not
   force already-agreed decisions through another planning interview.
2. Write a concise Markdown brief inside the request's `brief` string. Include
   the outcome, per-repo deliverables, acceptance checks, decisions, open questions,
   source references, and what must not be published. Distinguish existing practice
   from proposals. Reference durable evidence instead of copying private excerpts.
   Include cross-repo link dependencies and public URL expectations when relevant.
3. Write JSON to the exact `Project handoff request file` path in launch context:

   ```json
   {
     "action": "create",
     "task": "pkm-talk",
     "repositories": ["presentations", "website"],
     "brief": "# Outcome\n...\n\n# Deliverables and checks\n..."
   }
   ```

   `task` is lowercase kebab-case, at most 64 characters. `repositories` contains
   one to eight eligible names or exact paths; the first is the receiving session's
   home. Use only `action`, `task`, `repositories`, and `brief`; no shell commands
   or paths to external brief files. Keep the brief under 64,000 characters.
   For adding repositories, use `action: "add"`, the current named task, and only
   the new repositories. The brief should preserve the task's objective and explain
   the addition. An ordinary unnamed session cannot use `add`.
4. Summarize the proposed destinations and ask the user to type `/project-handoff`.
   Stop there. The command displays the resolved write roots and brief for one
   explicit confirmation. On approval the host launcher creates worktrees and
   switches sessions; cancellation keeps the current session. Do not invoke the
   command yourself or use bash, terminal automation, or another agent to approve.

The request is a proposal, never authorization. It is temporary until approved;
the approved brief is then stored outside project repos. The command grants task
worktree writes, not publication, commits, credential access, or changes to PARA.
If the launch context has no request path/eligible list, report that this requires
a fresh `pi-task` session rather than inventing access or installing software.
