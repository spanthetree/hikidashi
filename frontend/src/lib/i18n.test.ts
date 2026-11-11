import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "svelte/compiler";
import { describe, expect, it } from "vitest";
import { groupLabel, stateLabel, tagLabel, tagName } from "./i18n";
import { m } from "./paraglide/messages";

const root = join(import.meta.dirname, "..", "..");
const read = (path: string) => readFileSync(join(root, path), "utf8");

function files(dir: string, ext: string): string[] {
  return readdirSync(join(root, dir), { withFileTypes: true, recursive: true })
    .filter((e) => e.isFile() && e.name.endsWith(ext) && !e.parentPath.includes("paraglide"))
    .map((e) => join(e.parentPath, e.name).slice(root.length + 1));
}

/* ---------- nothing hard-coded ---------- */

// Attributes a person reads or hears. Others (class, href, type) are code.
const VISIBLE = new Set(["title", "placeholder", "aria-label", "aria-description", "alt", "label"]);
// The name is not translated.
const BRAND = /\bHikidashi\b/g;
const wordy = (text: string) => /\p{L}/u.test(text.replace(BRAND, ""));

type Node = { type?: string; name?: string; data?: string; start?: number; [k: string]: unknown };

/** Text in the markup that is not a message: "Save" rather than {m.save()}.
    Skips what is meant literally: <code>, and anything inside translate="no". */
function literals(source: string): string[] {
  const found: string[] = [];
  const at = (n: Node) => `${source.slice(0, n.start).split("\n").length}`;

  const literal = (n: Node) =>
    n.name === "code" || (n.attributes as Node[] | undefined)?.some((a) =>
      a.type === "Attribute" && a.name === "translate"
      && Array.isArray(a.value) && (a.value as Node[])[0]?.data === "no");

  function walk(n: unknown) {
    if (!n || typeof n !== "object") return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    const node = n as Node;
    // style:x="{n}ms", class:x and the rest are code, like most attributes.
    if (node.type === "Comment" || node.type?.endsWith("Directive") || literal(node)) return;
    if (node.type === "Text" && wordy(node.data ?? "")) found.push(`${at(node)}: ${node.data!.trim()}`);
    if (node.type === "Attribute") {
      if (!VISIBLE.has(node.name!) || !Array.isArray(node.value)) return;
      for (const v of node.value as Node[]) {
        if (v.type === "Text" && wordy(v.data ?? "")) found.push(`${at(v)}: ${node.name}="${v.data}"`);
      }
      return;
    }
    for (const [k, v] of Object.entries(node)) if (k !== "metadata") walk(v);
  }

  walk(parse(source, { modern: true }).fragment);
  return found;
}

describe("every user-visible string is a message", () => {
  for (const file of files("src", ".svelte")) {
    it(file, () => { expect(literals(read(file))).toEqual([]); });
  }

  it("catches what it should", () => {
    expect(literals(`<p>Save</p><input placeholder="Search…"><code>./hikidashi tag</code>
      <span translate="no">fetch_v</span><b>&times;</b><img alt="">`))
      .toEqual(["1: Save", '1: placeholder="Search…"']);
  });
});

/* ---------- the translations ---------- */

type Messages = Record<string, unknown>;
const messages = (locale: string): Messages => JSON.parse(read(`messages/${locale}.json`));
const en = messages("en");
const translations = (JSON.parse(read("project.inlang/settings.json")).locales as string[])
  .filter((l) => l !== "en");
const keys = (msgs: Messages) => Object.keys(msgs).filter((k) => k !== "$schema").sort();

/** The {inputs} a message's text uses, across all its variants. */
const inputs = (msg: unknown) =>
  [...new Set(JSON.stringify(msg).match(/(?<!\\)\{[a-zA-Z]\w*\}/g))].sort();

describe("messages", () => {
  for (const locale of translations) {
    const msgs = messages(locale);

    it(`${locale} has every English message, and nothing else`, () => {
      expect(keys(msgs)).toEqual(keys(en));
    });

    it(`each ${locale} message takes the same inputs as the English`, () => {
      for (const k of keys(en)) expect([k, inputs(msgs[k])]).toEqual([k, inputs(en[k])]);
    });
  }

  it("each message is used", () => {
    const code = [...files("src", ".svelte"), ...files("src", ".ts")].map(read).join("\n");
    const unused = keys(en).filter((k) => !new RegExp(`\\bm\\.${k}\\b`).test(code));
    expect(unused).toEqual([]);
  });

  it("counts in each language", () => {
    expect(m.ingredient_count({ count: 1 })).toBe("1 ingredient");
    expect(m.ingredient_count({ count: 1200 })).toBe("1,200 ingredients");
    expect(m.ingredient_count({ count: 1 }, { locale: "ja" })).toBe("材料1点");
    expect(m.ingredient_count({ count: 1200 }, { locale: "ja" })).toBe("材料1,200点");
  });
});

describe("our vocabularies", () => {
  it("are shown in the language on screen, and anything else as it is", () => {
    expect(tagLabel("Home & DIY")).toBe("Home & DIY");
    expect(m.tag_home_diy({}, { locale: "ja" })).toBe("住まい・DIY");
    expect(tagLabel("my own tag")).toBe("my own tag");
    expect(tagLabel("constructor")).toBe("constructor");
    expect(groupLabel("Fish & seafood")).toBe("Fish & seafood");
    expect(stateLabel("tagged")).toBe("tagged");
  });

  it("read a typed category back to its name", () => {
    expect(tagName(" travel ")).toBe("Travel");
    expect(tagName("Woodworking")).toBe("Woodworking");
  });
});
