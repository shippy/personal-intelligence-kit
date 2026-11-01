import { existsSync } from "node:fs";

export default function requireSeatbelt(pi) {
  pi.on("tool_call", () => {
    const profile = process.env.PI_SEATBELT_PROFILE;
    if (process.env.PI_SEATBELT_PROFILE_SCOPE !== "tool-subprocess-v1" || !profile || !existsSync(profile)) {
      return { block: true, reason: "pi-task requires an active Seatbelt profile; restart after fixing the sandbox error." };
    }
  });
}
