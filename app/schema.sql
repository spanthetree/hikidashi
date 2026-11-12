-- Hikidashi schema. One row per reel, keyed by the Instagram shortcode.
-- Rows are never deleted on re-import; an import only ever adds or widens.
--
-- `item` and what hangs off it (tags from hashtags and the model, the model's
-- recipe, vectors) is a shared cache: it depends only on the shortcode, so a
-- reel two people saved is fetched, transcribed and tagged once. What belongs
-- to a person is that they saved it, and what they did with it: user_item,
-- collection, import_run, and the `user` rows of recipe.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS item (
  shortcode     TEXT PRIMARY KEY,        -- parsed from permalink, the spine of the system
  permalink     TEXT NOT NULL,
  author        TEXT,                    -- the @handle, from the Owner group
  author_name   TEXT,                    -- display name
  saved_at      INTEGER,                 -- unix seconds; earliest save by anyone wins
  fbid          TEXT,                    -- Instagram's internal media id
  caption       TEXT,                    -- present in the export, mojibake repaired
  hashtags      TEXT,                    -- JSON array, as posted; never rewritten
  clean_hashtags TEXT,                   -- JSON array: normalised, noise dropped, deduped
  transcript    TEXT,
  ocr_text      TEXT,
  summary       TEXT,                    -- one sentence, from the LLM tagger
  keywords      TEXT,                    -- JSON array, free text from the LLM tagger
  primary_tag   TEXT,                    -- tag.name of the item's one category; see item_tag
  game          TEXT,                    -- the video game it shows, when sure; app/tagging/games.py
  game_guess    TEXT,                    -- a game it may show, for its savers to confirm
  thumb_path    TEXT,                    -- relative to HIKIDASHI_MEDIA
  media_path    TEXT,                    -- original mp4, never re-encoded
  bytes         INTEGER,
  duration      INTEGER,                 -- seconds, from the fetch
  width         INTEGER,
  height        INTEGER,
  like_count    INTEGER,
  comment_count INTEGER,
  raw_meta      TEXT,                    -- the export record, verbatim
  fetch_meta    TEXT,                    -- the yt-dlp response, verbatim
  state         TEXT NOT NULL DEFAULT 'imported',
  fail_stage    TEXT,
  fail_reason   TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  first_seen_in INTEGER REFERENCES import_run(id),

  -- Which build of each stage produced the columns above. Bumping one of these
  -- is how a re-run costs only what actually changed.
  fetch_v       INTEGER NOT NULL DEFAULT 0,
  asr_v         INTEGER NOT NULL DEFAULT 0,
  ocr_v         INTEGER NOT NULL DEFAULT 0,
  tag_v         INTEGER NOT NULL DEFAULT 0,  -- the LLM tagger; pairs with tag_profile
  hashtag_v     INTEGER NOT NULL DEFAULT 0,  -- the hashtag pass, free to re-run
  embed_v       INTEGER NOT NULL DEFAULT 0,
  recipe_v      INTEGER NOT NULL DEFAULT 0,  -- the recipe pass, recipes or not
  place_v       INTEGER NOT NULL DEFAULT 0,  -- places read from the caption, any or none

  -- Which hardware profile produced them. asr_v alone cannot tell you whether a
  -- transcript came from the small model or the large one. A hosted API's
  -- output is stamped provider:model instead, as groq:whisper-large-v3-turbo
  -- (#116), so reels made by an older model can be queued again.
  asr_profile   TEXT,
  tag_profile   TEXT,
  recipe_profile TEXT,
  ocr_profile   TEXT,                  -- NULL: tesseract; else provider:model
  -- The language a multilingual model heard, as an ISO 639-1 code. NULL for the
  -- local models, which are English-only and are told so.
  asr_language  TEXT
);

CREATE INDEX IF NOT EXISTS item_state_idx    ON item(state);
CREATE INDEX IF NOT EXISTS item_saved_at_idx ON item(saved_at DESC);
CREATE INDEX IF NOT EXISTS item_author_idx   ON item(author);
CREATE INDEX IF NOT EXISTS item_tag_v_idx    ON item(tag_v) WHERE state != 'failed';
CREATE INDEX IF NOT EXISTS item_primary_tag_idx ON item(primary_tag);
-- /media/<path> looks its reel up by path, to check who may have it.
CREATE INDEX IF NOT EXISTS item_thumb_path_idx ON item(thumb_path);
CREATE INDEX IF NOT EXISTS item_media_path_idx ON item(media_path);

-- The photos of a carousel post, in order, when every slide is one. thumb_path
-- is still the first slide's, for the grid; these are for paging through.
CREATE TABLE IF NOT EXISTS item_slide (
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  position  INTEGER NOT NULL,            -- from 0
  path      TEXT NOT NULL,               -- relative to HIKIDASHI_MEDIA
  PRIMARY KEY (shortcode, position)
);
CREATE INDEX IF NOT EXISTS item_slide_path_idx ON item_slide(path);

-- The people who use this library. User 1 always exists: it owned the library
-- before there were accounts, and with HIKIDASHI_AUTH=none every request is it.
CREATE TABLE IF NOT EXISTS user (
  id            INTEGER PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT,                    -- NULL: cannot sign in with a password
  ig_handle     TEXT,                    -- self-declared, never verified
  is_admin      INTEGER NOT NULL DEFAULT 0,
  disabled      INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL
);

-- A person's library: the reels they saved. Everything a request can see goes
-- through here, and a reel nobody has saved any more is deleted with its media.
CREATE TABLE IF NOT EXISTS user_item (
  user_id       INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  shortcode     TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  saved_at      INTEGER,                 -- unix seconds; their earliest save wins
  first_seen_in INTEGER REFERENCES import_run(id),
  tag_id        INTEGER REFERENCES tag(id) ON DELETE SET NULL,  -- the tag they chose
  game          TEXT,                    -- the game they say it shows; '' for none
  PRIMARY KEY (user_id, shortcode)
);
CREATE INDEX IF NOT EXISTS user_item_saved_idx ON user_item(user_id, saved_at DESC);
CREATE INDEX IF NOT EXISTS user_item_sc_idx    ON user_item(shortcode);

-- Signed-in browsers. The cookie holds a random token and only its SHA-256 is
-- kept, so this table cannot be used to sign in. Deleting a row signs out.
CREATE TABLE IF NOT EXISTS session (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_user_idx ON session(user_id);

-- There is no open sign-up: an admin makes a one-time link, kept the same way.
CREATE TABLE IF NOT EXISTS invite (
  id         INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  created_by INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_by    INTEGER REFERENCES user(id) ON DELETE SET NULL,
  used_at    INTEGER
);

-- Provenance separated from truth: a tag knows where it came from, so a re-tag
-- can wipe every model tag and leave every hand-written one untouched.
CREATE TABLE IF NOT EXISTS tag (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'user'       -- taxonomy | user
);

-- Each automatic source's one answer to "what is this reel?". One row per
-- source, so a pass that re-runs replaces its own answer and nothing else. The
-- tag a person chose is theirs alone, in user_item.tag_id.
CREATE TABLE IF NOT EXISTS item_tag (
  shortcode  TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  tag_id     INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  source     TEXT NOT NULL,               -- hashtag | model | game
  confidence REAL,
  PRIMARY KEY (shortcode, source)
);
CREATE INDEX IF NOT EXISTS item_tag_source_idx ON item_tag(source);

-- item.primary_tag follows item_tag: the game pass's (Gaming, for a sure game
-- on a reel filed elsewhere), else the model's, else the hashtag pass's. A
-- reel shows its saver's own tag over this one; see app/library.py. Kept by
-- triggers rather than a view so that it can be indexed for search like any
-- other column, whoever writes item_tag. app/db.py rebuilds them when the
-- order changes.
CREATE TRIGGER IF NOT EXISTS item_tag_ai AFTER INSERT ON item_tag BEGIN
  UPDATE item SET primary_tag = (
    SELECT tag.name FROM item_tag JOIN tag ON tag.id = item_tag.tag_id
    WHERE item_tag.shortcode = new.shortcode
    ORDER BY CASE item_tag.source WHEN 'game' THEN 0 WHEN 'model' THEN 1 ELSE 2 END
    LIMIT 1)
  WHERE shortcode = new.shortcode;
END;

CREATE TRIGGER IF NOT EXISTS item_tag_ad AFTER DELETE ON item_tag BEGIN
  UPDATE item SET primary_tag = (
    SELECT tag.name FROM item_tag JOIN tag ON tag.id = item_tag.tag_id
    WHERE item_tag.shortcode = old.shortcode
    ORDER BY CASE item_tag.source WHEN 'game' THEN 0 WHEN 'model' THEN 1 ELSE 2 END
    LIMIT 1)
  WHERE shortcode = old.shortcode;
END;

CREATE TRIGGER IF NOT EXISTS item_tag_au AFTER UPDATE ON item_tag BEGIN
  UPDATE item SET primary_tag = (
    SELECT tag.name FROM item_tag JOIN tag ON tag.id = item_tag.tag_id
    WHERE item_tag.shortcode = new.shortcode
    ORDER BY CASE item_tag.source WHEN 'game' THEN 0 WHEN 'model' THEN 1 ELSE 2 END
    LIMIT 1)
  WHERE shortcode = new.shortcode;
END;

-- Recipes read out of reels by app/recipes. Like item_tag.source, the model's
-- output and your corrections are separate rows: a re-extract replaces only the
-- 'model' row, and your 'user' row, when there is one, is the recipe you see.
-- Only reels found to be recipes have a row; item.recipe_v records that the
-- rest were read.
CREATE TABLE IF NOT EXISTS recipe (
  shortcode     TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  source        TEXT NOT NULL,              -- model | user
  user_id       INTEGER NOT NULL DEFAULT 0, -- whose correction; 0 on the model's
  title         TEXT,
  servings      INTEGER,
  time_minutes  INTEGER,
  steps         TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
  incomplete    INTEGER NOT NULL DEFAULT 0, -- the rest is elsewhere: "link in bio"
  hidden        INTEGER NOT NULL DEFAULT 0, -- yours only: not a recipe after all
  confidence    REAL,
  source_fields TEXT,                       -- JSON: caption, transcript, ocr
  dropped       TEXT,                       -- JSON: ingredients no source names
  updated_at    INTEGER NOT NULL,
  PRIMARY KEY (shortcode, source, user_id)
);

CREATE TABLE IF NOT EXISTS recipe_ingredient (
  shortcode  TEXT NOT NULL,
  source     TEXT NOT NULL,
  user_id    INTEGER NOT NULL DEFAULT 0,
  position   INTEGER NOT NULL,
  item       TEXT NOT NULL,                 -- English, lowercase: "wheat noodles"
  quantity   TEXT,                          -- as written: "1½", "a splash"
  unit       TEXT,
  note       TEXT,
  as_written TEXT,                          -- the source's own words for it
  PRIMARY KEY (shortcode, source, user_id, position),
  FOREIGN KEY (shortcode, source, user_id) REFERENCES recipe(shortcode, source, user_id)
    ON DELETE CASCADE
);

-- Each person's shopping list (#48): the recipes on it, and the lines they
-- have ticked off. The lines themselves are not stored. They are worked out
-- from the recipes each time, so a corrected recipe corrects the list, and a
-- tick is kept by the line's name (app/recipes/shopping.py).
CREATE TABLE IF NOT EXISTS shopping_recipe (
  user_id   INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  added_at  INTEGER NOT NULL,
  PRIMARY KEY (user_id, shortcode)
);

CREATE TABLE IF NOT EXISTS shopping_tick (
  user_id INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  line    TEXT NOT NULL,                  -- the line's key: "garlic clove"
  PRIMARY KEY (user_id, line)
);

-- The places a reel's caption names, as app/places reads them: "📍Mabel's
-- Deli — 118 Water St". Only reels that name one have rows;
-- item.place_v records that the rest were read.
CREATE TABLE IF NOT EXISTS item_place (
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  seq       INTEGER NOT NULL,             -- from 0, in caption order
  text      TEXT NOT NULL,                -- as the caption has it; geocode's key
  PRIMARY KEY (shortcode, seq)
);
CREATE INDEX IF NOT EXISTS item_place_text_idx ON item_place(text);

-- Where each place is, from the geocoder. Keyed by the text rather than the
-- reel, so a place twenty reels name is looked up once. A row with no lat was
-- looked up and not found; no row at all, not looked up yet.
CREATE TABLE IF NOT EXISTS geocode (
  text         TEXT PRIMARY KEY,
  lat          REAL,
  lng          REAL,
  label        TEXT,                      -- the geocoder's name for what it found
  kind         TEXT,                      -- its type: restaurant, city, peak
  confidence   REAL,                      -- 0-1; see app/places/geocoder.py
  query        TEXT,                      -- the variant of the text that found it
  near         TEXT,                      -- the box a second look kept to (refine)
  geo_v        INTEGER NOT NULL,
  looked_up_at INTEGER NOT NULL
);

-- Instagram's own collections, kept distinct from our taxonomy. Each person's
-- come from their own export.
CREATE TABLE IF NOT EXISTS collection (
  user_id   INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  name      TEXT NOT NULL,
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  PRIMARY KEY (user_id, name, shortcode)
);
CREATE INDEX IF NOT EXISTS collection_sc_idx ON collection(shortcode);

-- Every import is recorded so "which ZIP introduced this reel" is answerable,
-- and so re-importing a byte-identical export is detectable before parsing it.
CREATE TABLE IF NOT EXISTS import_run (
  id            INTEGER PRIMARY KEY,
  user_id       INTEGER NOT NULL DEFAULT 1, -- who imported it; see app/db.py
  source_name   TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  parser        TEXT,                     -- which parse strategy matched
  seen          INTEGER NOT NULL DEFAULT 0,
  inserted      INTEGER NOT NULL DEFAULT 0,
  updated       INTEGER NOT NULL DEFAULT 0,
  unparsed      INTEGER NOT NULL DEFAULT 0,
  collections   INTEGER NOT NULL DEFAULT 0
);

-- One vector per item for semantic search, alongside FTS rather than instead of
-- it. A plain table, not a vec0 one: the schema has to apply on a connection
-- with no extension loaded, and at a few hundred rows a brute-force
-- vec_distance_cosine scan is instant. `digest` is of the text that was
-- embedded, so a new summary or caption re-embeds that item and nothing else.
CREATE TABLE IF NOT EXISTS item_vec (
  shortcode TEXT PRIMARY KEY REFERENCES item(shortcode) ON DELETE CASCADE,
  digest    TEXT NOT NULL,
  embedding BLOB NOT NULL               -- float32, the layout sqlite-vec reads
);

-- More vectors per item, one per stretch of its transcript and on-screen text,
-- for "that video where he says..." searches: item_vec is of the summary, which
-- keeps what a reel is about and drops what was said. Written with item_vec,
-- whose digest covers these too.
CREATE TABLE IF NOT EXISTS item_chunk_vec (
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  seq       INTEGER NOT NULL,
  embedding BLOB NOT NULL,
  PRIMARY KEY (shortcode, seq)
);

-- The JSON arrays are indexed as they are stored: the tokenizer splits on the
-- quotes and commas, so ["brake pads","jdm"] indexes brake, pads and jdm.
-- Adding a column means dropping this table and its triggers: see app/db.py.
CREATE VIRTUAL TABLE IF NOT EXISTS item_fts USING fts5(
  caption, transcript, ocr_text, summary, primary_tag, clean_hashtags, keywords, game,
  content='item', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS item_ai AFTER INSERT ON item BEGIN
  INSERT INTO item_fts(rowid, caption, transcript, ocr_text, summary,
                       primary_tag, clean_hashtags, keywords, game)
  VALUES (new.rowid, new.caption, new.transcript, new.ocr_text, new.summary,
          new.primary_tag, new.clean_hashtags, new.keywords, new.game);
END;

CREATE TRIGGER IF NOT EXISTS item_ad AFTER DELETE ON item BEGIN
  INSERT INTO item_fts(item_fts, rowid, caption, transcript, ocr_text, summary,
                       primary_tag, clean_hashtags, keywords, game)
  VALUES ('delete', old.rowid, old.caption, old.transcript, old.ocr_text, old.summary,
          old.primary_tag, old.clean_hashtags, old.keywords, old.game);
END;

CREATE TRIGGER IF NOT EXISTS item_au AFTER UPDATE ON item BEGIN
  INSERT INTO item_fts(item_fts, rowid, caption, transcript, ocr_text, summary,
                       primary_tag, clean_hashtags, keywords, game)
  VALUES ('delete', old.rowid, old.caption, old.transcript, old.ocr_text, old.summary,
          old.primary_tag, old.clean_hashtags, old.keywords, old.game);
  INSERT INTO item_fts(rowid, caption, transcript, ocr_text, summary,
                       primary_tag, clean_hashtags, keywords, game)
  VALUES (new.rowid, new.caption, new.transcript, new.ocr_text, new.summary,
          new.primary_tag, new.clean_hashtags, new.keywords, new.game);
END;

-- Recipe titles and ingredients, so "gochujang" finds a reel whose caption
-- never says it. One row per recipe row that is not hidden: the model's under
-- user_id 0, each correction under its author, and a search uses yours over the
-- model's. Kept by app/recipes/service.py rather than triggers, since a row
-- needs its ingredient list.
CREATE VIRTUAL TABLE IF NOT EXISTS recipe_fts USING fts5(
  shortcode UNINDEXED, user_id UNINDEXED, title, ingredients
);

-- The always-on workers' claims (#72; app/jobs). A worker leases a few reels
-- for one stage and renews the lease as it works. One it stops renewing
-- expires, and its reels go back to the pool, so a worker killed mid-batch
-- loses nothing. Which reels a stage has to do is its own pending query.
CREATE TABLE IF NOT EXISTS job_lease (
  shortcode  TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  stage      TEXT NOT NULL,             -- enrich | asr | ocr | tag | recipe | embed
  worker     TEXT NOT NULL,             -- job_worker.name
  expires_at INTEGER NOT NULL,          -- unix seconds
  PRIMARY KEY (shortcode, stage)
);
CREATE INDEX IF NOT EXISTS job_lease_worker_idx ON job_lease(worker);

-- How often a stage has tried a reel without finishing it, by failing or by
-- dying with it leased. At the cap the reel is failed for that stage and not
-- offered again until the stage's version changes or someone retries it.
CREATE TABLE IF NOT EXISTS job_attempt (
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  stage     TEXT NOT NULL,
  version   INTEGER NOT NULL,             -- the stage's version they were made at
  attempts  INTEGER NOT NULL,
  reason    TEXT,                         -- the last one's
  last_at   INTEGER NOT NULL,
  PRIMARY KEY (shortcode, stage)
);

-- Each worker's last check-in, so that a worker that is off looks different
-- from one that is broken: stopped_at is set by a clean exit, and a worker
-- that stops checking in without it has crashed or been killed.
CREATE TABLE IF NOT EXISTS job_worker (
  name       TEXT PRIMARY KEY,          -- HIKIDASHI_WORKER_NAME, else the host's name
  pid        INTEGER,
  stages     TEXT NOT NULL,             -- JSON array: the stages it runs
  started_at INTEGER NOT NULL,
  seen_at    INTEGER NOT NULL,
  stopped_at INTEGER,
  doing      TEXT,                      -- the stage it is working on now
  problems   TEXT                       -- JSON {stage: why}: stages it cannot run now
);

-- Bearer tokens for whatever is not a browser (#103), shown once and kept as a
-- hash, like sessions; revoking one deletes it. Two kinds that never stand in
-- for each other: `api`, which a person makes for their own library with the
-- scopes they pick and which lasts 30 days at most, and `worker`, which an
-- admin makes for a worker on another machine (#35) and which lasts until
-- revoked. A token goes with its owner's account.
CREATE TABLE IF NOT EXISTS api_token (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('api', 'worker')),
  user_id      INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,           -- what it is for, as "desktop"
  scopes       TEXT NOT NULL,           -- space-separated, as "read:library"
  token_hash   TEXT NOT NULL UNIQUE,
  created_at   INTEGER NOT NULL,
  expires_at   INTEGER,                 -- NULL for a worker token
  last_used_at INTEGER                  -- to the minute
);
CREATE INDEX IF NOT EXISTS api_token_user_idx ON api_token(user_id);

-- Hosted AI (#116, #125; app/ai). Settings for each user's own API and, with
-- user_id NULL, the server's: per stage a provider, model and base URL, keys
-- (encrypted, app/ai/secrets.py), the monthly budget, the switch and why it
-- went off, and how a user's reels are processed. value is JSON. Environment
-- variables override the server's (app/ai/config.py), and never switch it on.
CREATE TABLE IF NOT EXISTS setting (
  user_id    INTEGER REFERENCES user(id) ON DELETE CASCADE,  -- NULL: the server's
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  updated_by INTEGER                       -- user.id; NULL from the system
);
CREATE UNIQUE INDEX IF NOT EXISTS setting_key_idx ON setting(ifnull(user_id, 0), key);

-- Every change to those settings and every switch on or off: who, what, when.
-- Never a key: a key change says which provider's key, and nothing of it.
CREATE TABLE IF NOT EXISTS ai_audit (
  id       INTEGER PRIMARY KEY,
  at       INTEGER NOT NULL,
  actor_id INTEGER,                         -- NULL: the system (an automatic off)
  user_id  INTEGER,                         -- whose API; NULL: the server's
  action   TEXT NOT NULL,                   -- as "set asr", "key set openai", "on", "off"
  detail   TEXT
);
CREATE INDEX IF NOT EXISTS ai_audit_at_idx ON ai_audit(at DESC);

-- What each API (api:server, api:user:<id>) knows of a reel (#125): when it
-- first found it ready, for the server's grace period; its API errors, counted
-- apart from workers' attempts and never failing the reel; and whether it was
-- left to workers, when a switch went on for new reels only.
CREATE TABLE IF NOT EXISTS api_reel (
  api       TEXT NOT NULL,
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  seen_at   INTEGER,
  errors    INTEGER NOT NULL DEFAULT 0,
  reason    TEXT,                          -- the last error's
  last_at   INTEGER,
  left      INTEGER NOT NULL DEFAULT 0,    -- 1: the backlog, left to workers
  PRIMARY KEY (api, shortcode)
);

-- What every hosted request used, and an estimate of its cost (#125), against
-- whoever's API made it: a user's, the server's, or a hosted worker on another
-- machine (worker set), which reports its own. The monthly budget is checked
-- against this.
CREATE TABLE IF NOT EXISTS hosted_usage (
  id            INTEGER PRIMARY KEY,
  at            INTEGER NOT NULL,
  api           TEXT NOT NULL,             -- api:server | api:user:<id> | hosted:<worker>
  user_id       INTEGER,                   -- the API's owner; NULL for the server's
  shortcode     TEXT,                      -- the reel it was for
  stage         TEXT NOT NULL,
  model         TEXT NOT NULL,             -- provider:model
  requests      INTEGER NOT NULL DEFAULT 1,
  input_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  audio_seconds REAL NOT NULL DEFAULT 0,
  images        INTEGER NOT NULL DEFAULT 0,
  usd           REAL NOT NULL DEFAULT 0    -- estimated, from the price table in app/ai/usage.py
);
CREATE INDEX IF NOT EXISTS hosted_usage_api_idx ON hosted_usage(api, at);
