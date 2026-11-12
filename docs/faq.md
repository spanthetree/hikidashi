# FAQ

[Back to the README](../README.md)

- [Does it need my Instagram password?](#does-it-need-my-instagram-password)
- [Is this allowed?](#is-this-allowed)
- [What leaves my machine?](#what-leaves-my-machine)
- [What hardware do I need?](#what-hardware-do-i-need)
- [Do I need a GPU?](#do-i-need-a-gpu)
- [How much disk space does it use?](#how-much-disk-space-does-it-use)
- [Which languages does it understand?](#which-languages-does-it-understand)
- [How do new saves get in?](#how-do-new-saves-get-in)
- [What if a post is deleted from Instagram?](#what-if-a-post-is-deleted-from-instagram)
- [Can several people use one server?](#can-several-people-use-one-server)
- [Can I use it on my phone?](#can-i-use-it-on-my-phone)
- [How do I back it up?](#how-do-i-back-it-up)

## Does it need my Instagram password?

No. Hikidashi starts from the data export Instagram gives you, and downloads
each post from its public link. It never signs in to Instagram, so posts that
need a login (private accounts, some restricted posts) can't be downloaded;
their captions are still searchable.

## Is this allowed?

Hikidashi archives your own saves, from your own data export, for your own
use. It downloads posts with [yt-dlp](https://github.com/yt-dlp/yt-dlp), as
you could yourself, and gets around no access controls. You are responsible
for how you use it and what you keep: the posts are other people's work, and
Instagram's terms apply to you as they always do.

Hikidashi is not affiliated with, endorsed by or sponsored by Instagram or
Meta.

## What leaves my machine?

By default, only:

- the downloads of the posts you saved, from Instagram;
- the model weights, downloaded once from where their authors publish them
  ([THIRD_PARTY.md](../THIRD_PARTY.md));
- the published Docker images, if you use them.

If you turn on the map, place names from captions go to the geocoder you
choose, and your browser loads map tiles; both can be self-hosted
([places and the map](using.md#places-and-the-map)). Transcription, on-screen
text, tagging, recipes and search all run locally. Hikidashi itself sends no
telemetry. [Architecture](architecture.md#context) lists every outside call.

## What hardware do I need?

A 64-bit machine (x86-64 or ARM64) with Docker, or Python 3.12. As a rough
guide:

- **The server alone** (search, the web app, downloads): about 1 GB of
  memory. A NAS or an ARM board is enough
  ([on a low-power device](install.md#with-docker)).
- **Everything on one machine, on the CPU:** 8 GB of memory or more, mostly
  for the tagging model. Several cores help: transcription runs about 25×
  faster than real time on 28 threads.

## Do I need a GPU?

No. Everything runs on the CPU, more slowly, with smaller models. An NVIDIA
GPU speeds up transcription and tagging and lets Hikidashi use larger, more
accurate models: on an RTX 3080, 4.4 hours of audio transcribed in 5 minutes
([hardware profiles](pipeline.md#hardware-profiles)). The GPU doesn't have to
be in the server: a desktop can do the heavy work for a NAS, whenever it is
on ([remote workers](remote-workers.md)).

## How much disk space does it use?

About 10 MB per reel for the video and thumbnail, so a few GB for a few
hundred saves. The models take another 2 to 7 GB, depending on the profile.
The database itself stays small.

## Which languages does it understand?

The interface is in English and Japanese. Search by meaning is multilingual: a
Japanese search finds English reels on the same subject, and the other way
round. Transcription uses English-only Whisper models for now, so speech in
other languages isn't transcribed well; captions and on-screen text in any
language are still searched.

## How do new saves get in?

From a new Instagram export: import it whenever you like, and reels already in
the library are skipped. Instagram offers no way for an outside app to read
your saves directly, so continuous sync is
[still open](https://github.com/spanthetree/hikidashi/issues/9).

## What if a post is deleted from Instagram?

Once Hikidashi has downloaded a reel, the copy is yours and stays in the
library. Posts that were already gone before the first download keep their
caption from the export.

## Can several people use one server?

Yes. With accounts on, everyone gets a private library, and reels saved by
more than one person are downloaded and processed once
([accounts and access](accounts.md)).

## Can I use it on my phone?

Yes, in the phone's browser: the web app works at phone width. There is no
app to install.

## How do I back it up?

Everything is in the data folder (`./data`, or `HIKIDASHI_DATA`): the
database, the media and the models. Stop the server and workers before
copying it, since a copy of a SQLite file taken mid-write can be torn; a
filesystem snapshot (ZFS, Btrfs) needs no stop. The models can be left out,
as they download again; the media too, but only while the posts are still on
Instagram. [Backing up on Unraid](install-unraid.md#backing-up) has more.
