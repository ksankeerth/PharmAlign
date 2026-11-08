import { describe, expect, it } from "vitest";
import { checkStarters, whyQuestion } from "../lib/assistantStarters";
import { stream } from "../api/transport";

describe("case starters", () => {
  it("read as sentences for every status", () => {
    expect(whyQuestion("SAME-01", "look")).toBe("Why does SAME-01 need a look?");
    expect(whyQuestion("LBL-02", "person_confirms")).toBe("Why does LBL-02 need a person to confirm it?");
    expect(whyQuestion("QUAL-07", "held")).toBe("Why is QUAL-07 held?");
    expect(whyQuestion("PV-01", "confirmed")).toBe("Why is PV-01 confirmed?");
    expect(whyQuestion("X-1", "error")).toBe("Why did X-1 end in an error?");
  });

  it("never reads a status as a bare word after 'is'", () => {
    for (const status of ["look", "person_confirms", "pass", "waived", "skipped", "error", "running"] as const) {
      expect(whyQuestion("C-1", status)).not.toMatch(new RegExp(`^Why is C-1 ${status.replace("_", " ")}\\?$`));
    }
  });

  it("offers why, read again and draft for the check", () => {
    expect(checkStarters("QUAL-07", "held")).toEqual(["Why is QUAL-07 held?", "Read the pages cited by QUAL-07 again", "Draft a comment on QUAL-07"]);
  });
});

describe("the answer stream", () => {
  it("skips the comment lines the agent sends to keep a long turn alive", async () => {
    const body = [
      ": keepalive\n\n",
      ': keepalive\n\ndata: {"type":"text_delta","text":"Hi"}\n\n',
      ": keepalive\n\n",
      'data: {"type":"error","code":"assistant_unavailable","message":"The assistant took too long to answer. Please try again.","retryable":true}\n\n',
    ];
    const encoder = new TextEncoder();
    const original = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            for (const part of body) controller.enqueue(encoder.encode(part));
            controller.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      )) as typeof fetch;
    try {
      const got: string[] = [];
      for await (const event of stream("post_api_v1_assistant_sessions_session_id_turns", { path: { session_id: "as-1" }, body: { message: "Hi", focus: null } })) {
        got.push(event.type);
      }
      expect(got).toEqual(["text_delta", "error"]);
    } finally {
      globalThis.fetch = original;
    }
  });
});
