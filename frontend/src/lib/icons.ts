/* Every icon twice: a line drawing on a 16x16 grid, stroked, and an 8x8
   pixel version for the pixel-art switch, filled. In a grid, X is a pixel
   that is on. Icon.svelte picks between them. */

const GRIDS = {
  menu: `
........
XXXXXXXX
........
XXXXXXXX
........
XXXXXXXX
........
........`,
  person: `
...XX...
..XXXX..
..XXXX..
...XX...
........
.XXXXXX.
XXXXXXXX
XXXXXXXX`,
  palette: `
..XXXX..
.X....X.
X.X..X.X
X......X
X.X....X
X...XXX.
.X..X...
..XX....`,
  close: `
X......X
.X....X.
..X..X..
...XX...
...XX...
..X..X..
.X....X.
X......X`,
  prev: `
.....X..
....XX..
...XX...
..XX....
..XX....
...XX...
....XX..
.....X..`,
  next: `
..X.....
..XX....
...XX...
....XX..
....XX..
...XX...
..XX....
..X.....`,
  pin: `
..XXXX..
..XXXX..
..XXXX..
.XXXXXX.
XXXXXXXX
...XX...
...XX...
...XX...`,
  library: `
XXX..XXX
XXX..XXX
XXX..XXX
........
........
XXX..XXX
XXX..XXX
XXX..XXX`,
  time: `
..XXXX..
.X....X.
X..X...X
X..X...X
X..XXX.X
X......X
.X....X.
..XXXX..`,
  without: `
..XXXX..
.X...XX.
X...X..X
X..X...X
X.X....X
XX.....X
.X....X.
..XXXX..`,
  with: `
.....XXX
...XXXXX
..XXXXXX
.XXXXXX.
.XXXXX..
.XXX....
X.......
X.......`,
  collections: `
.XXXXXX.
.XXXXXX.
.XXXXXX.
.XXXXXX.
.XXXXXX.
.XXXXXX.
.XX..XX.
.X....X.`,
  tags: `
XXXX....
XXXXX...
XX.XXX..
XXXXXXX.
.XXXXXXX
..XXXXX.
...XXX..
....X...`,
  pipeline: `
..X.....
..X.....
.X.X....
.X.X....
X..X..XX
...X.X..
....XX..
....X...`,
  language: `
..XXXX..
.X.XX.X.
X..XX..X
XXXXXXXX
X..XX..X
X..XX..X
.X.XX.X.
..XXXX..`,
  place: `
..XXXX..
.XX..XX.
.XX..XX.
..XXXX..
..XXXX..
...XX...
...XX...
........`,
  map: `
..XXXX..
.XX..XX.
.XX..XX.
..XXXX..
X..XX..X
XX.XX.XX
X.X..X.X
XXXXXXXX`,
  locate: `
...XX...
..X..X..
.X....X.
XX.XX.XX
XX.XX.XX
.X....X.
..X..X..
...XX...`,
  basket: `
..X..X..
.X....X.
XXXXXXXX
X......X
X.X..X.X
X.X..X.X
.X....X.
..XXXX..`,
  export: `
...XX...
..XXXX..
.X.XX.X.
...XX...
...XX...
X..XX..X
X......X
XXXXXXXX`,
  import: `
...XX...
...XX...
...XX...
.X.XX.X.
..XXXX..
X..XX..X
X......X
XXXXXXXX`,
  shuffle: `
.....XX.
XX..XXXX
..XX..X.
...X....
...X....
..XX..X.
XX..XXXX
.....XX.`,
};

export type IconName = keyof typeof GRIDS;

const ring = "M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0Z";

const LINES: Record<IconName, string> = {
  menu: "M2 4h12M2 8h12M2 12h12",
  person: "M10.8 5.5a2.8 2.8 0 1 1-5.6 0 2.8 2.8 0 0 1 5.6 0ZM2.5 14c.8-3 3-4.4 5.5-4.4s4.7 1.4 5.5 4.4",
  palette: "M8 2a6 6 0 1 0 0 12c.9 0 1.2-.6 1-1.3-.3-.9.2-1.7 1.2-1.7H12a2 2 0 0 0 2-2A6 6 0 0 0 8 2Z"
    + "M5 7h.01M8 5h.01M11 7h.01",
  close: "M3.5 3.5l9 9M12.5 3.5l-9 9",
  prev: "M10 3.3 5.3 8l4.7 4.7",
  next: "M6 3.3 10.7 8 6 12.7",
  pin: "M6 2.5h4l-.5 4 2 2.5H4.5l2-2.5zM8 9v4.5",
  library: "M2.5 2.5h4.5v4.5h-4.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5h-4.5zM9 9h4.5v4.5H9z",
  time: `${ring}M8 5v3.2l2 1.3`,
  without: `${ring}M4.1 11.9l7.8-7.8`,
  with: "M3 13c0-6 4-9.5 10-10-.5 6-4 10-10 10ZM3 13l5-5",
  collections: "M4 2.5h8v11l-4-3-4 3z",
  tags: "M2.5 2.5h5.2l6 6-5.2 5.2-6-6zM5.4 5.4h.01",
  pipeline: "M1.5 8h3l2-4.5 3 9 2-4.5h3",
  language: `${ring}M2.5 8h11M8 2.5c1.8 2 1.8 9 0 11M8 2.5c-1.8 2-1.8 9 0 11`,
  place: "M8 14s-4.5-4.6-4.5-8a4.5 4.5 0 0 1 9 0c0 3.4-4.5 8-4.5 8ZM9.6 6a1.6 1.6 0 1 1-3.2 0 1.6 1.6 0 0 1 3.2 0Z",
  map: "M2 4.2 6 2.5l4 1.7 4-1.7v9.3l-4 1.7-4-1.7-4 1.7zM6 2.5v9.3M10 4.2v9.3",
  locate: "M12 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM8 1.5V4M8 12v2.5M1.5 8H4M12 8h2.5",
  basket: "M5.5 2.5 3.5 6M10.5 2.5l2 3.5M1.5 6h13l-1.5 7.5H3zM6 8.5v3M10 8.5v3",
  export: "M8 10V2M5 5l3-3 3 3M2.5 9.5v3a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-3",
  import: "M8 2v8M5 7l3 3 3-3M2.5 9.5v3a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-3",
  shuffle: "M1.5 4.5h3c3 0 4 7 7 7h3M1.5 11.5h3c1.2 0 2-1 2.7-2.3M9 6.8c.7-1.3 1.5-2.3 2.5-2.3h3"
    + "M12.5 2.5l2 2-2 2M12.5 9.5l2 2-2 2",
};

/** A grid as one path: a rectangle per horizontal run of pixels. */
function pixels(grid: string): string {
  return grid.trim().split("\n").flatMap((row, y) =>
    [...row.matchAll(/X+/g)].map((run) => `M${run.index} ${y}h${run[0].length}v1h-${run[0].length}z`),
  ).join("");
}

export const ICONS = Object.fromEntries(
  (Object.keys(GRIDS) as IconName[]).map((name) => [name, { line: LINES[name], pixel: pixels(GRIDS[name]) }]),
) as Record<IconName, { line: string; pixel: string }>;
