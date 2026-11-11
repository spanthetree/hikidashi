import { importExport, NetworkError } from "./api";
import { m } from "./paraglide/messages";
import type { ImportResult } from "./types";

/* Export ZIPs on their way in. They belong to the page, not to the import
   box: one dropped anywhere on the library and one chosen in the toolbar's
   dialog join the same queue, and the box shows it wherever it is open.
   Several go one after another, so each gets the server to itself. */

export interface Upload {
  id: number;
  file: File;
  /** waiting its turn, sending (`sent` of it so far), the server reading it,
      done, failed (worth trying again) or refused (not a ZIP, so not sent). */
  state: "waiting" | "sending" | "reading" | "done" | "failed" | "refused";
  sent: number;
  result?: ImportResult;
  error?: string;
}

/** By its name, as the server checks: the browser's type for a ZIP varies. */
export const isZip = (file: File) => file.name.toLowerCase().endsWith(".zip");

const ZIP_TYPES = new Set(["application/zip", "application/x-zip-compressed",
                           "application/x-zip", "multipart/x-zip"]);

/** Whether a drag carries files. Its names are hidden until the drop. */
export const hasFiles = (dt: DataTransfer | null) => !!dt?.types.includes("Files");

/* While dragging, only the types show, and not always: an empty one may still
   be a ZIP, so only one that names something else counts against it. */
export function draggingNonZip(dt: DataTransfer | null) {
  return [...(dt?.items ?? [])].some((i) => i.kind === "file" && i.type && !ZIP_TYPES.has(i.type));
}

const ACTIVE: Upload["state"][] = ["waiting", "sending", "reading"];

/** Still to finish: waiting, sending or being read. */
export const active = (u: Upload) => ACTIVE.includes(u.state);

class Imports {
  list = $state<Upload[]>([]);

  #id = 0;
  #running = false;
  #listeners = new Set<(u: Upload) => void>();

  get busy() {
    return this.list.some(active);
  }

  /** Called with each upload once it lands; returns the way to stop. */
  listen(fn: (u: Upload) => void) {
    this.#listeners.add(fn);
    return () => { this.#listeners.delete(fn); };
  }

  add(files: Iterable<File>) {
    for (const file of files) {
      const zip = isZip(file);
      this.list.push({
        id: ++this.#id, file, sent: 0,
        state: zip ? "waiting" : "refused",
        error: zip ? undefined : m.import_not_zip({ file: file.name }),
      });
    }
    void this.#next();
  }

  retry(id: number) {
    const u = this.list.find((x) => x.id === id);
    if (!u || u.state !== "failed") return;
    Object.assign(u, { state: "waiting", sent: 0, error: undefined });
    void this.#next();
  }

  dismiss(id: number) {
    const i = this.list.findIndex((x) => x.id === id);
    if (i >= 0 && !active(this.list[i])) this.list.splice(i, 1);
  }

  async #next() {
    if (this.#running) return;
    this.#running = true;
    try {
      let u: Upload | undefined;
      while ((u = this.list.find((x) => x.state === "waiting"))) {
        const up = u;
        up.state = "sending";
        try {
          up.result = await importExport(up.file, (f) => {
            up.sent = f;
            if (f >= 1) up.state = "reading";
          });
          up.state = "done";
        } catch (err) {
          up.state = "failed";
          up.error = m.import_failed({
            error: err instanceof NetworkError ? m.import_network() : (err as Error).message,
          });
        }
        for (const fn of this.#listeners) fn(up);
      }
    } finally {
      this.#running = false;
    }
  }
}

export const imports = new Imports();
