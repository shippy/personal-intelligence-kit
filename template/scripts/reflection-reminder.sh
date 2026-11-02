#!/bin/bash
# Weekly reflection reminder, run by launchd (com.claude.reflection-reminder) on Sundays.
# No Python, no config parsing, no secrets: the reflections dir is the only argument.
# Notifies when no *-weekly-reflection.md in REFLECTIONS_DIR was modified within 6 days;
# otherwise exits silently. A missing dir counts as "not written".
#
# Usage: reflection-reminder.sh REFLECTIONS_DIR
# REFLECTION_REMINDER_NOTIFIER overrides the terminal-notifier binary (tests).
set -euo pipefail

dir="${1:?usage: reflection-reminder.sh REFLECTIONS_DIR}"
notifier="${REFLECTION_REMINDER_NOTIFIER:-terminal-notifier}"

if [ -d "$dir" ] && [ -n "$(find "$dir" -maxdepth 1 -type f -name '*-weekly-reflection.md' -mmin -8640 -print -quit)" ]; then
  exit 0
fi

exec "$notifier" -title Claude -subtitle "Weekly reflection" \
  -message "Not written yet this week — run batch: weekly-reflection" \
  -group claude-reflections -sound default
