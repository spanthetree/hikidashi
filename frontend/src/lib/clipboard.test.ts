import { afterEach, describe, expect, it, vi } from "vitest";
import { copyFailure, copyText } from "./clipboard";

/* The unit tests run in Node, with no DOM: each test puts in just the
   browser it needs (a secure context or not, a clipboard or not, an
   execCommand that works or not). */

type Fake = { value: string; focus: () => void; select: () => void; remove: () => void };

function browser({ secure, clipboard, exec }: {
  secure: boolean;
  clipboard?: (text: string) => Promise<void>;
  exec?: () => boolean;
}) {
  const copied: string[] = [];
  let area: Fake | null = null;
  vi.stubGlobal("isSecureContext", secure);
  vi.stubGlobal("navigator", clipboard ? { clipboard: { writeText: clipboard } } : {});
  vi.stubGlobal("document", {
    body: { appendChild: () => {} },
    activeElement: { focus: vi.fn() },
    createElement: () => {
      area = { value: "", focus: () => {}, select: () => {}, remove: () => {}, setAttribute: () => {}, style: {} } as Fake;
      return area;
    },
    execCommand: (what: string) => {
      if (what !== "copy" || !exec) throw new Error("not supported");
      const ok = exec();
      if (ok && area) copied.push(area.value);
      return ok;
    },
  });
  return copied;
}

function field() {
  return { focus: vi.fn(), select: vi.fn() } as unknown as HTMLInputElement & {
    focus: ReturnType<typeof vi.fn>; select: ReturnType<typeof vi.fn>;
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("copyText", () => {
  it("uses the clipboard API in a secure context", async () => {
    const writes: string[] = [];
    const legacy = browser({ secure: true, clipboard: async (t) => { writes.push(t); }, exec: () => true });
    expect(await copyText("hk_token")).toBe("copied");
    expect(writes).toEqual(["hk_token"]);
    expect(legacy).toEqual([]);
  });

  it("falls back to execCommand over plain HTTP, where there is no clipboard API", async () => {
    const legacy = browser({ secure: false, exec: () => true });
    expect(await copyText("http://192.168.1.20:8080/invite/abc")).toBe("copied");
    expect(legacy).toEqual(["http://192.168.1.20:8080/invite/abc"]);
  });

  it("falls back too when the clipboard API refuses", async () => {
    const legacy = browser({
      secure: true,
      clipboard: () => Promise.reject(new Error("NotAllowedError")),
      exec: () => true,
    });
    expect(await copyText("a note")).toBe("copied");
    expect(legacy).toEqual(["a note"]);
  });

  it("selects the field when both fail", async () => {
    browser({ secure: false, exec: () => false });
    const input = field();
    expect(await copyText("hk_token", input)).toBe("selected");
    expect(input.focus).toHaveBeenCalled();
    expect(input.select).toHaveBeenCalled();
  });

  it("says it failed when both fail and there is nothing to select", async () => {
    browser({ secure: false });
    expect(await copyText("milk, eggs")).toBe("failed");
  });
});

describe("copyFailure", () => {
  it("says the text is selected, and that HTTPS would fix it", () => {
    browser({ secure: false });
    const said = copyFailure("selected");
    expect(said).toMatch(/selected/);
    expect(said).toMatch(/HTTPS/);
  });

  it("leaves HTTPS out where the page already has it", () => {
    browser({ secure: true });
    expect(copyFailure("failed")).not.toMatch(/HTTPS/);
  });
});
