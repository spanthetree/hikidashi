import { getMe } from "./api";
import type { Me } from "./types";

/* Whoever is signed in, for the top bar and the admin-only links. Loaded
   once; with HIKIDASHI_AUTH=none it is the library's owner. */
class Session {
  me = $state<Me | null>(null);
  #loading: Promise<void> | null = null;

  load() {
    this.#loading ??= getMe().then((me) => { this.me = me; }, () => { this.#loading = null; });
    return this.#loading;
  }

  set(me: Me) { this.me = me; }
}

export const session = new Session();
