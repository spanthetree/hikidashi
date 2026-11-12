# Installing on TrueNAS SCALE

Hikidashi on TrueNAS SCALE 24.10 ("Electric Eel") or later is one compose
file, [`deploy/truenas/hikidashi.yaml`](../deploy/truenas/hikidashi.yaml),
pasted into *Install via YAML*. The three containers in it share one
dataset. This page covers the install, the first run, adding a GPU or a
remote worker, updating, backing up, and what goes wrong.
[ADR 0012](adr/0012-nas-installs-templates-and-yaml.md) says why this way,
and not a catalog app. [Unraid](install-unraid.md) ·
[Back to the README](../README.md)

> The file pulls the published images
> (`ghcr.io/spanthetree/hikidashi-server`, `-worker`, `-worker-cuda`), which
> each release publishes (#77), at `:latest`.

## What's in the file

| Service | Image | What it does | Needed |
|---|---|---|---|
| `app` | `hikidashi-server` | the web UI and API, import, the map | always |
| `server-worker` | `hikidashi-server` | fetches each new reel, and embeds reels for search by meaning | always |
| `worker` | `hikidashi-worker` | transcribes, reads text on screen, tags, finds recipes | unless a GPU desktop does it all |

Tags and recipes need an Ollama. The simplest is TrueNAS's own **Ollama**
app, which the worker reaches through the host on its port, 30068. Without
one, set the worker's `HIKIDASHI_LOCAL_STAGES` to `asr,ocr`.

## Before you start

**Make a dataset for the data.** In **Datasets**, select the pool (or its
`apps` dataset, if you keep one), then **Add Dataset**:

- **Name:** `hikidashi`, so that it's `/mnt/tank/apps/hikidashi`, say.
- **Dataset Preset:** **Apps**. That gives TrueNAS's `apps` user (568), which
  the containers run as, permission to write.

It must be a dataset on this machine, never an SMB or NFS share mounted from
elsewhere. SQLite's locks aren't kept reliably over a network filesystem, and
with three containers writing at once the database can be corrupted. Plan on
about 10 MB a reel, plus 1.5–3 GB of model weights.

## Installing

1. Open [`hikidashi.yaml`](../deploy/truenas/hikidashi.yaml) and copy it into
   an editor. YAML depends on its indentation, and TrueNAS's box is a poor
   place to edit it.
2. Change `/mnt/tank/apps/hikidashi` to your dataset's path. It appears
   **three times**, once per service, and all three must match exactly.
3. Choose the settings in `app`'s `environment`. `HIKIDASHI_AUTH` is `none`
   if you're the only user, or `local` for accounts. Port `8080` can be
   changed on the left of `"8080:8080"`.
4. For the always-on-NAS-and-GPU-desktop layout, delete the `worker` service,
   or set its stages to `asr,ocr`. See
   [A GPU desktop](#a-gpu-desktop-as-a-remote-worker).
5. **Apps** → **Discover Apps** → **⋮** → **Install via YAML**. Name it
   `hikidashi`, paste the file into **Custom Config**, and **Save**.

TrueNAS pulls the images and starts the three containers. The `app`
container shows as healthy once the web UI answers. The containers don't
need to start in any order: they wait for each other over the database.

## First run

Open `http://<truenas>:8080`.

- **With `HIKIDASHI_AUTH: local`**, the library has no owner yet. The setup
  link is in the `app` container's log (**Apps** → `hikidashi` →
  **Workloads** → `app` → **View Logs**): `Claim the library at
  /setup?token=…`. Open that link and set the first admin's name and
  password. The link changes each time the container starts, and stops
  working once someone has claimed the library.
- **Import:** drop the Instagram export ZIP onto the page (the
  [Installing](install.md#getting-your-instagram-export) says how to get it). Search works on
  captions straight away. The workers then fetch, transcribe and tag in the
  background. The **Pipeline** page shows how far they've got.

On their first reels, the server-worker downloads the embedder (about 1.2 GB)
and the worker downloads Whisper (330 MB on the CPU, 1.5 GB for CUDA), both
into the dataset's `models/`.

Commands that have no page, such as `hikidashi user`, run in the `app`
container. TrueNAS names it after the app, as `ix-hikidashi-app-1`:

```sh
sudo docker exec -it ix-hikidashi-app-1 hikidashi user passwd <name>
sudo docker ps --filter name=ix-hikidashi   # the names, if yours differ
```

## Adding a GPU

An NVIDIA card goes to the worker. TrueNAS installs the driver itself
(**Apps** → **Configuration** → **Settings** → **Install NVIDIA Drivers**).
25.10 supports Turing and newer: Pascal, Maxwell and Volta cards no longer
work.

1. Find the card's UUID: `nvidia-smi -L` in a shell (`GPU-…`).
2. **Apps** → `hikidashi` → **Edit**. In the `worker` service:
   - change the image to `hikidashi-worker-cuda`, with the same tag
   - set `HIKIDASHI_PROFILE: cuda`
   - remove `cpus: 2`
   - uncomment the `deploy` block, and add the two `NVIDIA_` variables to
     `environment`, with your UUID in both places
3. **Save**. Give the Ollama app the card too, in its own settings.

If you change cards later, change the UUID here as well. TrueNAS doesn't
update it for a custom app. See #64 for what waits on a GPU.

## A GPU desktop as a remote worker

The desktop works for this server over HTTP, and has no database of its own.

1. Delete the `worker` service, or set its stages to `asr,ocr` to transcribe
   on the CPU while the desktop is off.
2. In the web UI, **Pipeline** → make a worker token. (Or, before the first
   sign-in, `sudo docker exec -it ix-hikidashi-app-1 hikidashi token make
   desktop --worker`.)
3. On the desktop, follow [remote workers](remote-workers.md), with
   `HIKIDASHI_SERVER=http://<truenas>:8080` and the token.

The desktop has to run the same release as the server.

## Updating

The images follow `:latest`. TrueNAS pulls all three of an app's images
together, so the server and its worker, which must run the same release, stay
on one. When **Apps** shows an update for `hikidashi`, **Update** pulls them
and restarts the app. To stay on one release instead, **Edit** the app and
replace `latest` with it, such as `0.19.0`, on **all three** images. The database catches up
with a new release on its own when the app starts, and the workers re-run
only the stages whose version changed. [CHANGELOG.md](../CHANGELOG.md) lists
what changed.

## Backing up

Everything is in the dataset. A **periodic snapshot task** on it is the
easiest backup, and needs nothing stopped. A snapshot is taken at one
instant, and SQLite recovers from that the way it recovers from a power cut.
Replicate the snapshots to another pool or machine for a copy that survives
the pool. To copy the files themselves instead, stop the app first, since a
copy of a SQLite file taken mid-write can be torn.

`models/` and `cache/` can be left out. They're downloaded again.
`media/`, the videos and thumbnails, is most of the size. It can be fetched
again too, but only while the posts are still on Instagram.

## The fallback: the Custom App form

**Install via YAML** is the way to install. The **Custom App** form can do
the same, one container at a time, three times over: the image, `568:568`
under **Security Context**, the dataset as a host path at `/data` on each,
and `hikidashi worker --log server-worker.log` as the server-worker's
command. Use it only if YAML is unavailable.

## Troubleshooting

- **`Permission denied` or `readonly database` in a log.** The dataset
  doesn't let `apps` write, or its files belong to another user, such as
  root from an earlier install. Stop the app, then, in a shell:
  `sudo chown -R 568:568 /mnt/tank/apps/hikidashi`. Or edit the dataset's
  permissions to give `apps` full control, applied recursively.
- **`database disk image is malformed`, or locks that never clear.** The
  path is a share mounted from another machine. Move the data to a local
  dataset.
- **Reels import but never get thumbnails.** The `server-worker` service is
  missing, or its path differs from `app`'s. The **Pipeline** page lists the
  workers that have checked in, and what each runs: one has to run *enrich*.
- **A worker runs but never picks anything up.** Its dataset path differs
  from `app`'s, so it has a database of its own with nothing in it.
- **The Pipeline page says *tag cannot run* or *recipe cannot run*.** The
  worker can't reach Ollama: check the Ollama app is running, and its port
  in the worker's `OLLAMA_URL`. Or set the stages to `asr,ocr`.
- **TrueNAS rejects the YAML.** The indentation was changed while pasting.
  Paste it again from an editor, and keep the app name lowercase.
