#!/usr/bin/env python3
"""corpus — manage the Chinese corpus and its flat search index.

Two jobs:

  corpus.py add FILE [FILE ...]
      Copy each file INTO the corpus root (default ~/Chinese Text Analysis),
      then normalise it straight into the index. Run it from anywhere -- this is
      the "I found a new text, file it" command.

  corpus.py build [PATH ...]
      (Re)build the index from files already in the corpus. With no PATH it
      normalises the whole corpus root. Run after editing the cleaner, or to
      refresh everything.

The index lives in a `_index` folder beside these scripts (kept out of the corpus so
cloud-sync clients don't try to sync thousands of generated files), one same-stem file
per source, so opusmine reads the Source for free from the filename. It is a DERIVED
artifact: safe to delete and rebuild. Originals are never modified; use `rch` on the
corpus root when you want the untrimmed, original-charset line in full context.

Design choices baked in here:
  - Charset is left AS-IS (no opencc). Cross-charset matching is done on the
    query side in opusmine, so a traditional source stays traditional and
    produces a traditional card.
  - Lines are NOT sentence-split. Splitting at ingest loses context like
    "别碰那个！很危险！"; trimming is deferred to retrieval, where the target
    word is known and the trim can be length-gated.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

HOME = Path.home()
DEFAULT_CORPUS = HOME / "Chinese Text Analysis"  # corpus root; originals live here
INDEX_DIRNAME = "_index"                         # the index directory's name
INDEX_DIR = Path(__file__).resolve().parent / INDEX_DIRNAME   # ...and it lives beside the scripts
TEXT_SUFFIXES = {".txt", ".md"}                  # which files in the corpus get indexed

# Directories that build/add pretend don't exist. Each entry is either a single folder
# NAME (matched anywhere in a path, e.g. "old") or a relative PATH of consecutive folders
# (e.g. "hsr/Quest_Dialogues", matched as a run of components — so it won't catch some
# other stray "Quest_Dialogues"). This is the junk-drawer switch: dump unwanted text in a
# folder named here and the index stays clean without any command-line flags. Edit freely.
IGNORE_DIRS = {
    INDEX_DIRNAME,        # never re-index the index itself
    "old",
    "Anki_dump",
    "hsr/Quest_Dialogues",
    "current",
    "genshin/world_quest_corpus",
    "Scripts",
    "nontext",
    # add your own cruft folders, e.g.:
    # "raw_dumps", "scratch", "hsr_dump", "_attic",
}

# --- cleaning rules ----------------------------------------------------------
# CJK = any line WITHOUT one of these characters is dropped. This single test
# removes sprite/image ids (avg_npc_864_1#5$1, 34_g10_tent_inside, 43_i01, ...).
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
FENCE_RE = re.compile(r"^:::")                       # pandoc fenced-div marker line ( ::: description )
IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")         # markdown image:  ![](path)
ATTR_RE = re.compile(r"\{\.[^}]*\}")                # stray pandoc attrs:  {.image} {.background}
HEADER_RE = re.compile(r"^#{1,6}\s*")               # markdown headers:  ## 13-2 ...
BOLD_RE = re.compile(r"\*\*([^*]*)\*\*")            # **bold** -> bold (also de-bolds speaker labels)
ID_RE = re.compile(r"^\[.*?\]\s*")                 # leading bracketed id:  [146782006]
TAG_RE = re.compile(r"<[^>]+>")                    # html/unity tags:  <color=#dbc291ff> </color>
ESC_RE = re.compile(r"\\([^\w\s])")               # un-escape backslashed punctuation:  \. -> .
DOTS_RE = re.compile(r"\.{3,}")                   # 3+ ascii dots -> a single ellipsis …
SPEAKER_ONLY_RE = re.compile(r'^[^\s：:]{1,10}[：:]$')  # a bare "name：" line, dropped as a label


def clean_line(line: str) -> str:
    """Strip markup/escapes from one raw line. Returns "" for lines to discard."""
    line = line.rstrip("\r\n")
    if FENCE_RE.match(line.strip()):
        return ""
    line = ID_RE.sub("", line)
    line = IMG_RE.sub("", line)
    line = HEADER_RE.sub("", line)
    line = BOLD_RE.sub(r"\1", line)
    line = TAG_RE.sub("", line)
    line = ATTR_RE.sub("", line)
    line = html.unescape(line)
    line = ESC_RE.sub(r"\1", line)
    line = DOTS_RE.sub("\u2026", line)
    # full-width space -> normal; tabs flattened so the TSV pipe downstream can't shift columns
    line = line.replace("\u3000", " ").replace("\t", " ").strip()
    return line


def keep(line: str) -> bool:
    """A cleaned line survives only if it has CJK and isn't a bare speaker label."""
    return bool(line) and bool(CJK.search(line)) and not SPEAKER_ONLY_RE.match(line)


def read_utf8(path: Path) -> str:
    """Read a corpus file strictly as UTF-8. A GBK/Big5 file read with errors='ignore'
    would silently lose most of its CJK, so refuse loudly instead."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        sys.exit(f"error: {path} is not valid UTF-8 ({e.reason} at byte {e.start}); "
                 f"convert it first, e.g.: iconv -f GB18030 -t UTF-8 '{path.name}'")


def normalize_file(path: Path) -> list[str]:
    """Clean a file into de-duped, index-ready lines (order preserved)."""
    raw = read_utf8(path)
    seen: set[str] = set()
    out: list[str] = []
    for ln in raw.splitlines():
        ln = clean_line(ln)
        if not keep(ln) or ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
    return out


MANIFEST_NAME = ".manifest.tsv"   # stem<TAB>resolved-source-path, one per line, inside the index dir


def load_manifest(index_dir: Path) -> dict[str, Path]:
    """The persisted stem->source map. Makes collision detection survive across runs,
    so a later `add` can never silently overwrite an earlier source's index file."""
    taken: dict[str, Path] = {}
    mf = index_dir / MANIFEST_NAME
    if mf.exists():
        for ln in mf.read_text(encoding="utf-8").splitlines():
            stem, _, src = ln.partition("\t")
            if stem and src:
                taken[stem] = Path(src)
    return taken


def save_manifest(index_dir: Path, taken: dict[str, Path]) -> None:
    lines = [f"{stem}\t{src}" for stem, src in sorted(taken.items())]
    (index_dir / MANIFEST_NAME).write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _stem_free(stem: str, path: Path, index_dir: Path, taken: dict[str, Path]) -> bool:
    """A stem is ours if the manifest maps it to this same source. Unmapped stems are
    free only if no index file exists (protects pre-manifest indexes too)."""
    reserved = taken.get(stem)
    if reserved is not None:
        return reserved == path
    return not (index_dir / f"{stem}.txt").exists()


def write_index(path: Path, index_dir: Path, taken: dict[str, Path]) -> tuple[Path, int] | None:
    """Normalise `path` and write <index_dir>/<stem>.txt. Returns (dest, n_lines).
    Re-indexing the same source overwrites its own file; a *different* source with a
    colliding stem gets a disambiguated name instead — never an overwrite."""
    lines = normalize_file(path)
    if not lines:
        return None
    path = path.resolve()
    stem = path.stem
    if not _stem_free(stem, path, index_dir, taken):
        base = f"{path.parent.name}_{path.stem}"
        stem, n = base, 2
        while not _stem_free(stem, path, index_dir, taken):
            stem = f"{base}_{n}"
            n += 1
        print(f"note: stem taken by another source; indexing {path.name} as {stem}.txt", file=sys.stderr)
    taken[stem] = path
    dest = index_dir / f"{stem}.txt"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest, len(lines)


def _segments(entry: str) -> tuple[str, ...]:
    """An ignore entry as path components: 'Scripts' -> ('Scripts',),
    'hsr/Quest_Dialogues' -> ('hsr', 'Quest_Dialogues')."""
    return tuple(s for s in entry.replace("\\", "/").strip("/").split("/") if s)


def _ignored(f: Path, index_dir: Path, excludes: list[str]) -> bool:
    if index_dir in f.parents:
        return True
    parts = f.parts
    for entry in IGNORE_DIRS:
        segs = _segments(entry)
        if not segs:
            continue
        if len(segs) == 1:
            if segs[0] in parts:                       # a single folder name, anywhere in the path
                return True
        else:
            n = len(segs)                              # a path like a/b: match consecutive components
            if any(parts[i:i + n] == segs for i in range(len(parts) - n + 1)):
                return True
    return any(ex in str(f) for ex in excludes)         # ad-hoc substring excludes


def iter_corpus_files(inputs: list[str], index_dir: Path, excludes: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        p = Path(item).expanduser()
        if p.is_dir():
            # recurse_symlinks: corpus dirs may be symlinks to trees kept outside
            # cloud sync (e.g. ~/Archive); without it rglob silently skips them
            for f in sorted(p.rglob("*", recurse_symlinks=True)):
                if f.is_file() and f.suffix in TEXT_SUFFIXES and not _ignored(f, index_dir, excludes):
                    files.append(f)
        elif p.is_file():
            if not _ignored(p, index_dir, excludes):
                files.append(p)
        else:
            print(f"skip (not found): {p}", file=sys.stderr)
    return files


def collect_text_files(items: list[str]) -> list[Path]:
    """Expand a list of files/dirs into the text files within (for --merge)."""
    out: list[Path] = []
    for item in items:
        p = Path(item).expanduser()
        if p.is_dir():
            out += [f for f in sorted(p.rglob("*", recurse_symlinks=True))
                    if f.is_file() and f.suffix in TEXT_SUFFIXES]
        elif p.is_file():
            out.append(p)
        else:
            print(f"skip (not found): {p}", file=sys.stderr)
    return out


def cmd_build(args: argparse.Namespace) -> int:
    index_dir = args.out or INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)
    # A full rebuild (no paths) cleans by default so stale sources can't linger; a
    # partial build keeps the rest of the index. --clean / --no-clean overrides either.
    full_rebuild = not args.paths
    clean = args.clean if getattr(args, "clean", None) is not None else full_rebuild
    if clean:
        removed = 0
        for old in index_dir.glob("*.txt"):
            old.unlink()
            removed += 1
        if removed:
            print(f"cleaned {removed} existing index files")
        taken: dict[str, Path] = {}
    else:
        taken = load_manifest(index_dir)
    inputs = args.paths or [str(args.corpus)]
    excludes = getattr(args, "exclude", []) or []
    for f in iter_corpus_files(inputs, index_dir, excludes):
        res = write_index(f, index_dir, taken)
        if res:
            dest, n = res
            print(f"{f.name}: {n} lines -> {dest.name}")
    save_manifest(index_dir, taken)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    args.corpus.mkdir(parents=True, exist_ok=True)
    index_dir = INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)
    taken = load_manifest(index_dir)

    # --merge: concatenate all inputs into ONE corpus file, then index as one source.
    # This stays consistent with `build`: the merged file is a normal corpus file, so a
    # later rebuild regenerates it as a single source (no per-scene index explosion).
    if args.merge:
        sources = collect_text_files(args.files)
        if not sources:
            print("nothing to merge", file=sys.stderr)
            return 1
        name = args.merge if args.merge.endswith((".txt", ".md")) else f"{args.merge}.txt"
        dest = args.corpus / name
        if dest.exists() and not args.force:
            print(f"refusing to overwrite existing {dest.name} (use --force)", file=sys.stderr)
            return 1
        blob = "\n".join(read_utf8(p) for p in sources)
        dest.write_text(blob, encoding="utf-8")
        print(f"merged {len(sources)} files -> corpus/{dest.name}")
        res = write_index(dest, index_dir, taken)
        if res:
            idest, n = res
            print(f"  indexed: {n} lines -> {idest.name}")
        else:
            print("  (no indexable lines found)")
        save_manifest(index_dir, taken)
        return 0

    rc = 0
    for item in args.files:
        src = Path(item).expanduser()
        if not src.is_file():
            print(f"skip (not a file): {src}", file=sys.stderr)
            rc = 1
            continue
        dest = args.corpus / src.name
        if dest.exists() and dest.resolve() != src.resolve():
            same = dest.read_bytes() == src.read_bytes()
            if not same and not args.force:
                print(f"refusing to overwrite existing {dest.name} (use --force)", file=sys.stderr)
                rc = 1
                continue
            if not same:
                shutil.copy2(src, dest)
                print(f"copied (overwrote) {src.name} -> corpus")
            else:
                print(f"already in corpus: {dest.name}")
        else:
            shutil.copy2(src, dest)
            print(f"copied {src.name} -> corpus")
        res = write_index(dest, index_dir, taken)
        if res:
            idest, n = res
            print(f"  indexed: {n} lines -> {idest.name}")
        else:
            print("  (no indexable lines found)")
    save_manifest(index_dir, taken)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="corpus",
        description="Manage the Chinese corpus and its flat search index.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  corpus.py add ~/Downloads/novel.txt     copy into corpus + index it\n"
            "  corpus.py build                         rebuild the whole index\n"
            "  corpus.py build ~/corpus/newbook.txt    (re)index one existing file\n"
        ),
    )
    sub = ap.add_subparsers(dest="cmd")

    def add_corpus_opt(p):
        p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS,
                       help=f"corpus root (default: {DEFAULT_CORPUS})")

    p_add = sub.add_parser("add", help="copy file(s) into the corpus and index them",
                           formatter_class=argparse.RawDescriptionHelpFormatter,
                           epilog=("examples:\n"
                                   "  corpus.py add novel.txt\n"
                                   "  corpus.py add ./hsr_dump_dir --merge 'Star Rail'\n"))
    p_add.add_argument("files", nargs="+", help="file(s) or dir(s) to ingest from anywhere")
    p_add.add_argument("--merge", metavar="NAME",
                       help="concatenate all inputs into one corpus file NAME and index as a single source")
    p_add.add_argument("--force", action="store_true", help="overwrite an existing corpus file of the same name")
    add_corpus_opt(p_add)
    p_add.set_defaults(func=cmd_add)

    p_build = sub.add_parser("build", help="(re)build the index from corpus files")
    p_build.add_argument("paths", nargs="*", help="files/dirs to index (default: whole corpus root)")
    p_build.add_argument("--out", type=Path, default=None, help="index dir (default: _index beside the scripts)")
    p_build.add_argument("--exclude", action="append", default=[], metavar="STR",
                         help="skip any path containing STR (repeatable), e.g. --exclude TurnBased")
    p_build.add_argument("--clean", action=argparse.BooleanOptionalAction, default=None,
                         help="delete existing *.txt in the index dir first "
                              "(default: clean on a full rebuild, keep on a partial one)")
    add_corpus_opt(p_build)
    p_build.set_defaults(func=cmd_build)

    args = ap.parse_args()
    if not args.cmd:                       # bare `corpus.py` == build everything
        args.corpus, args.paths, args.out = DEFAULT_CORPUS, [], None
        return cmd_build(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
