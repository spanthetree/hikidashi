# Installing on Unraid

Hikidashi on Unraid 7 is four small containers from the templates in
[`deploy/unraid/`](../deploy/unraid/), sharing one folder on a pool. This page
covers the install, the first run, adding a GPU or a remote worker, updating,
backing up, [moving a library](#moving-a-library-here) from another machine,
and what goes wrong. The alternative,
[Compose Manager](#the-alternative-compose-manager), runs our compose files
instead. [ADR 0012](adr/0012-nas-installs-templates-and-yaml.md) says why
these two and not others. [TrueNAS SCALE](install-truenas.md) ·
[Back to the README](../README.md)

> The templates pull the published images
> (`ghcr.io/spanthetree/hikidashi-server`, `-worker`, `-worker-cuda`), which
> each release publishes (#77). They aren't in Community Applications search
> yet (#102): install them by hand, as below.

## What you install

| Template | Image | What it does | Needed |
|---|---|---|---|
| **Hikidashi** | `hikidashi-server` | the web UI and API, import, the map | always |
| **Hikidashi-Server-Worker** | `hikidashi-server` | fetches each new reel, and embeds reels for search by meaning | always |
| **Hikidashi-Worker** | `hikidashi-worker` | transcribes, reads text on screen, tags, finds recipes, on the CPU | unless a GPU desktop does it all |
| **Hikidashi-Worker-CUDA** | `hikidashi-worker-cuda` | the same, on an NVIDIA card | instead of Hikidashi-Worker |
| **Ollama** (from CA) | `ollama/ollama` | the model that tags and finds recipes | for tags and recipes |

The first two always go together. Without the server-worker, reels import
and are never fetched. The workers don't talk to the server over the network:
they share its database, in the Data folder. So **every Hikidashi template
needs exactly the same Data path**, and nothing else connects them.

Three layouts, from simplest:

- **One box, no GPU:** the first three. With Ollama for tags and recipes, or
  set the worker's Stages to `asr,ocr` and do without.
- **One box with an NVIDIA card:** Hikidashi, Hikidashi-Server-Worker and
  Hikidashi-Worker-CUDA, and Ollama on the card too.
- **An always-on NAS and a GPU desktop:** Hikidashi and
  Hikidashi-Server-Worker here, and optionally Hikidashi-Worker with Stages
  `asr,ocr` to transcribe slowly while the desktop is off. The desktop runs a
  [remote worker](#a-gpu-desktop-as-a-remote-worker).

## Before you start

**Pick the Data folder on a pool.** The default is
`/mnt/cache/appdata/hikidashi`. Use `/mnt/<pool>/appdata/hikidashi` if your
pool has another name. **Never `/mnt/user/...`**, even for a share that
lives on the cache. `/mnt/user` is a FUSE filesystem, and SQLite's locks
aren't kept reliably over FUSE. With three containers writing at once, the
database can be corrupted. Plan on about 10 MB a reel, plus 1.5–3 GB of
model weights.

Create it, owned by `nobody:users`, which the containers run as:

```sh
mkdir -p /mnt/cache/appdata/hikidashi
chown 99:100 /mnt/cache/appdata/hikidashi
```

## Installing from the templates

Until Hikidashi is in Community Applications, copy the templates to the
flash drive, in a terminal on the server (**>_** in the top bar):

```sh
cd /boot/config/plugins/dockerMan/templates-user
for t in hikidashi hikidashi-server-worker hikidashi-worker hikidashi-worker-cuda; do
  wget -q "https://raw.githubusercontent.com/spanthetree/hikidashi/master/deploy/unraid/$t.xml" -O "my-$t.xml"
done
```

Then, for each container in your layout, in this order: **Docker** →
**Add Container** → pick it under **Template** → check the fields → **Apply**.

1. **Hikidashi.** Set **Data** to your folder and pick **Accounts**: `none`
   if you're the only one who uses it, `local` for accounts. The other
   settings are under **Show more settings**: a reverse proxy and the map.
2. **Hikidashi-Server-Worker.** The same **Data**. Nothing else to set.
3. **Hikidashi-Worker** (or **-CUDA**). The same **Data**. Set **Stages** to
   `asr,ocr` if there's no Ollama, and set **Ollama URL** if it's somewhere
   other than this server's Ollama app.

The order doesn't matter to Hikidashi, as the containers wait for each other
over the database. This order just puts the web UI up first.

Every template runs as `nobody:users` (`--user 99:100` in **Extra
Parameters**), and restarts if it stops (`--restart=unless-stopped`).

### Ollama

Install **Ollama** from Community Applications, and keep its port, 11434,
published. The worker reaches it through the host at
`http://host.docker.internal:11434`, so it can be started in any order too.
The worker pulls the model it needs on first use: `qwen2.5:3b` on the CPU,
`qwen2.5:7b` with the CUDA profile. On an i3 or N-series CPU, tagging is
slow. Tags and recipes are where a GPU, here or on a desktop, makes the
biggest difference.

## First run

Open the web UI: **Hikidashi** → **WebUI**, or `http://<server>:8080`.

- **With `Accounts` = `local`**, the library has no owner yet. The setup link
  is in Hikidashi's log (the container's **Logs**):
  `Claim the library at /setup?token=…`. Open that link and set the first
  admin's name and password. The link changes each time the container
  starts, and stops working once someone has claimed the library.
- **Import:** drop the Instagram export ZIP onto the page (the
  [Installing](install.md#getting-your-instagram-export) says how to get it). Search works on
  captions straight away. The workers then fetch, transcribe and tag in the
  background. The **Pipeline** page shows how far they've got.

On their first reels, the server-worker downloads the embedder (about
1.2 GB) and the worker downloads Whisper (330 MB on the CPU, 1.5 GB for
CUDA), both into `Data/models`.

Commands that have no page, such as `hikidashi user` or `hikidashi
places`, run in the containers:

```sh
docker exec -it Hikidashi hikidashi user passwd <name>
docker exec -it Hikidashi hikidashi --help
```

## Adding a GPU

The card goes to **Hikidashi-Worker-CUDA**, in place of Hikidashi-Worker.

1. Install the **Nvidia Driver** plugin from Community Applications, and
   reboot.
2. Copy the card's UUID from the plugin's page (`GPU-…`).
3. Stop and remove Hikidashi-Worker (keep its Data).
4. Add **Hikidashi-Worker-CUDA** with the same Data, and the UUID in **GPU**,
   or `all`. Its Extra Parameters already have `--runtime=nvidia`.
5. Give Ollama the card too, the same way, if it runs here.

See #64 for what waits on a GPU.

## A GPU desktop as a remote worker

The desktop works for this server over HTTP, and has no database of its own.

1. On the server, keep Hikidashi and Hikidashi-Server-Worker. Remove
   Hikidashi-Worker, or set its Stages to `asr,ocr` to transcribe on the CPU
   while the desktop is off. Under Compose Manager, that's the
   `server-only` stack folder, or `everything` with
   `HIKIDASHI_LOCAL_STAGES=asr,ocr`.
2. In the web UI, **Pipeline** → make a worker token. (Or, before the first
   sign-in, `docker exec -it Hikidashi hikidashi token make desktop
   --worker`.)
3. On the desktop, follow [remote workers](remote-workers.md), with
   `HIKIDASHI_SERVER=http://<server>:8080` and the token.

The desktop has to run the same release as the server.

## The alternative: Compose Manager

The **Compose Manager** plugin (from Community Applications) runs
Hikidashi's own compose files, so the whole stack is one thing to start,
stop and update. It pulls the same published images as the templates, and
needs a checkout only for the compose files and `.env`. This was checked on
**Compose Manager Plus**, a fork of it. The original plugin works the same
way where it matters here.

**The stack is never the checkout.** The plugin loads every `*compose*.yml`
in a stack's folder and passes each with `-f`. On the checkout, that merges
in `docker-compose.worker.yml`, which fails without `HIKIDASHI_SERVER`,
and `COMPOSE_FILE` in `.env` is never read. So the stack points at one of
the folders in [`deploy/unraid/compose-manager/`](../deploy/unraid/compose-manager/),
each holding one file that includes the checkout's compose files:

| Stack folder | Containers | For |
|---|---|---|
| `compose-manager/everything` | Hikidashi, its server-worker, the worker (and Ollama, under the `ai` profile) | one box, with or without Ollama |
| `compose-manager/server-only` | Hikidashi and its server-worker | a box whose heavy stages run on a [GPU desktop](#a-gpu-desktop-as-a-remote-worker) |

1. Install **Compose Manager Plus** (or **Compose Manager**).
2. Check out Hikidashi on the pool, next to its data, and make the Data
   folder:

   ```sh
   git clone https://github.com/spanthetree/hikidashi /mnt/cache/appdata/hikidashi-src
   cp /mnt/cache/appdata/hikidashi-src/deploy/unraid/compose.env /mnt/cache/appdata/hikidashi-src/.env
   mkdir -p /mnt/cache/appdata/hikidashi && chown 99:100 /mnt/cache/appdata/hikidashi
   ```

   With no `git` on the server (the **NerdTools** plugin has it), clone
   through Docker instead:
   `docker run --rm -v /mnt/cache/appdata:/git alpine/git clone https://github.com/spanthetree/hikidashi /git/hikidashi-src`.
3. Edit the checkout's `.env`: the Data folder, accounts, and the layout. It
   already sets `HIKIDASHI_USER=99:100`. Edit it there, not in the plugin's
   env editor, which keeps its own file.
4. Check the layout picks the containers you expect:

   ```sh
   cd /mnt/cache/appdata/hikidashi-src/deploy/unraid/compose-manager/server-only
   docker compose config --services     # app, server-worker
   ```

5. Pull the server image (and the worker's, for `everything`). Compose Up
   would pull them too, but a terminal shows the progress:

   ```sh
   docker compose pull
   ```

   To stay on one release, set `HIKIDASHI_VERSION` in `.env` to it, such as
   `0.19.0`; unset, it is `latest`. Building from the checkout instead
   (`docker compose build`) still works, and is what happens when there is
   nothing to pull.

6. In **Docker** → **Compose**, **Add New Stack**, named `hikidashi`, with
   its directory set to the stack folder:
   `/mnt/cache/appdata/hikidashi-src/deploy/unraid/compose-manager/server-only`
   (or `everything`). Don't use **Edit Stack** → **Compose File** on it: that
   edits the file in the checkout.
7. **Compose Up.** It asks which profiles to start, with *All
   profile-based services (\*)* ticked. Untick it, and leave `ai` unticked
   unless Ollama should run in this stack. Left ticked, it starts Ollama,
   and on `server-only` the worker too. Then, as for the templates, the
   [first run](#first-run).

**To update:** `git pull` in the checkout, for the compose files, then pull
the images:

```sh
cd /mnt/cache/appdata/hikidashi-src && git pull
cd deploy/unraid/compose-manager/server-only && docker compose pull
```

Then **Compose Up** on the stack, which recreates the containers on the new
images. Compose Up alone doesn't pull: the images already exist, so it
starts the old ones. Skip the original plugin's **Update Stack**: it runs
`up --build`, which builds from the checkout rather than pulling. The images
left behind go with `docker image prune`. A
[remote worker](remote-workers.md) has to be updated to the same release.

## Updating

Each container: **Docker** → the icon → **Check for Updates**, then
**Apply Update**. Update all of them together: the server and its workers
must run the same release. The database catches up with a new release on its
own when Hikidashi starts, and the workers re-run only the stages whose
version changed. [CHANGELOG.md](../CHANGELOG.md) lists what changed.

To stay on one release, change `:latest` to a version, such as `:0.12.0`, in
**Repository** on each template.

## Backing up

Everything is in the Data folder. To copy it, stop the three containers
first, since a copy of a SQLite file taken mid-write can be torn. The
**Appdata Backup** plugin stops them for you. A ZFS snapshot of the pool
needs no stop: it's taken at one instant, and SQLite recovers from that the
way it recovers from a power cut.

`Data/models` and `Data/cache` can be left out. They're downloaded again.
`Data/media`, the videos and thumbnails, is most of the size. It can be
fetched again too, but only while the posts are still on Instagram.

## Moving a library here

A library from another machine, a desktop running Hikidashi under uv or
Docker say, moves as files: the database, and the videos and thumbnails it
names, which it keeps relative to `media/`. Its accounts, passwords and
worker tokens come with it, and replace this server's. The server has to run
the same release as the library came from, or a newer one: it brings an
older database up to date when it starts.

1. **Stop Hikidashi on the old machine**, so nothing writes to the database,
   and take a single-file copy of it. The copy takes in what is still in its
   `-wal` file:

   ```sh
   ./hikidashi stop
   python3 -c "import sqlite3; s=sqlite3.connect('data/hikidashi.sqlite3'); d=sqlite3.connect('data/transfer.sqlite3'); s.backup(d); d.close()"
   ```

2. **Stop the containers here**, and remove the database a first start made.
   Its `-wal` and `-shm` go too: left beside the copy, SQLite would replay
   them into it.

   ```sh
   cd /mnt/cache/appdata/hikidashi
   rm -f hikidashi.sqlite3 hikidashi.sqlite3-wal hikidashi.sqlite3-shm
   ```

3. **Copy the database and the media**, from the old machine's `data/`.
   rsync needs SSH on the server (**Settings** → **Management Access** →
   **Use SSH**), and picks up where it left off if it stops. Leave out
   `logs/`, `run/` and the `hikidashi.pre-*.sqlite3` backups. `models/` can
   come too, or be downloaded again.

   ```sh
   cd data
   rsync -avP transfer.sqlite3 root@<server>:/mnt/cache/appdata/hikidashi/hikidashi.sqlite3
   rsync -avP media imports root@<server>:/mnt/cache/appdata/hikidashi/
   ```

4. **Give the files to `nobody`**, as rsync wrote them as root:

   ```sh
   chown -R 99:100 /mnt/cache/appdata/hikidashi
   ```

5. **Start the containers, and check** that the counts match the old
   machine's. Run the same line there, on `data/transfer.sqlite3`:

   ```sh
   docker exec Hikidashi python -c "import sqlite3; c=sqlite3.connect('file:/data/hikidashi.sqlite3?mode=ro', uri=True); print(c.execute('select count(*), max(saved_at) from item').fetchone(), c.execute('select count(*) from item where media_path is not null').fetchone())"
   ```

   Under Compose Manager the container is `hikidashi`. With accounts on,
   sign in as you did on the old machine.

Once the old machine's library is moved, leave its Hikidashi stopped, or
the two libraries drift apart. Importing a newer export here later is safe:
an import only adds reels and fills in what was missing, and never repeats
or resets one.

## Troubleshooting

- **`database disk image is malformed`, or locks that never clear.** The Data
  path is under `/mnt/user`. Stop everything, move the folder to the pool
  (`/mnt/cache/appdata/hikidashi`), and set the new path on every template.
  Then check the database, with only Hikidashi started. This prints `ok`
  when it survived:

  ```sh
  docker exec Hikidashi python -c "import sqlite3; print(sqlite3.connect('/data/hikidashi.sqlite3').execute('PRAGMA integrity_check').fetchone()[0])"
  ```
- **`Permission denied` or `readonly database` in a log.** The folder, or
  files in it, belong to another user, most often root from a version before
  the templates ran as `nobody`. Stop the containers, then
  `chown -R 99:100 /mnt/cache/appdata/hikidashi`.
- **Reels import but never get thumbnails.** There's no
  Hikidashi-Server-Worker, or it has a different Data path. The **Pipeline**
  page lists the workers that have checked in, and what each runs: one has
  to run *enrich*.
- **A worker runs but never picks anything up.** Its Data path differs from
  Hikidashi's, so it has a database of its own with nothing in it. Check
  that the paths match to the letter.
- **The Pipeline page says *tag cannot run* or *recipe cannot run*.** The
  worker can't reach Ollama: check that Ollama's port is published, and the
  worker's **Ollama URL**. Or set Stages to `asr,ocr`.
- **The setup link says it isn't this server's.** It's from an older start.
  Copy the newest one from Hikidashi's log.
