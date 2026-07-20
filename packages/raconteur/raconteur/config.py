from __future__ import annotations
import os
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
import yaml

from haarpi import config as haarpi_config

GLOBAL_CONFIG_PATH = haarpi_config.legacy_path("raconteur")
PROJECT_CONFIG_FILE = Path("paper") / "raconteur.yaml"


@dataclass
class BrainConfig:
    coordinator_model: str = "qwen3.6:27b-16k"
    worker_model: str = "llama3.1:8b"


@dataclass
class VenueConfig:
    """One venue a paper may be written FOR.

    An outline and a manuscript are specific to a venue — its length, its columns, its
    citation style, what it will and won't publish — so a project may carry several, and
    the conference→journal flow means one may descend from another (`extends`).

    Two fields carry the trust:

    ``origin`` — "author" when YOU declared this venue (a row you added to the slate, or a
      hand-edited entry). raconteur may add candidates and refresh its own, but it must
      never touch, downgrade, or delete a venue you named. Same rule as your prose.

    ``sources`` — where each format field came from: "cfp" (fetched from the venue's own
      call for papers / author guidelines), "analysis" (an LLM read its own venue analysis
      — plausible, unverified), or "author". A page limit the tool INFERRED from prose it
      wrote itself is not a page limit, and the prompts must be able to tell the difference
      between a spec and a guess.
    """
    name: str = ""
    kind: str = ""                     # journal | conference | workshop
    status: str = "candidate"          # candidate | selected | submitted | published | rejected
    url: str = ""                      # the CFP / author-guidelines page
    origin: str = "raconteur"          # author | raconteur
    extends: str = ""                  # this venue's paper derives from that venue's paper
    page_limit: int | None = None
    word_limit: int | None = None      # the MAXIMUM
    word_min: int | None = None        # the other end, where the CFP states a range
    citation_style: str = ""
    columns: int | None = None   # unknown until the CFP says
    abstract_limit: int | None = None
    required_sections: str = ""        # content the venue mandates: CCS, keywords, ethics…
    section_structure: str = ""        # the venue's own section ORDER, if it states one;
                                       # empty means IBMRDC (see raconteur.skeleton)
    anonymized: bool | None = None     # double-blind — strips authorship from the writing
    template_url: str = ""             # best-effort link to the template / author kit
    template_kind: str = ""            # latex-acm | latex-ieee | latex | word | overleaf | ""
    format_notes: str = ""
    sources: dict = field(default_factory=dict)   # field name -> cfp | analysis | author

    # The specs a WRITER is shown (format + mandated content). anonymized and the
    # template_* fields are handled apart: the first is a prose directive, the last
    # two feed the human template-fetch task, not the draft.
    SPEC_FIELDS = ("page_limit", "word_min", "word_limit", "citation_style", "columns",
                   "abstract_limit", "required_sections", "section_structure",
                   "format_notes")

    @property
    def by_author(self) -> bool:
        return (self.origin or "").lower() == "author"

    def spec_line(self, field_name: str) -> str:
        """A spec, with where it came from — or an honest 'unknown'.

        The tool must never present a guess as a fact. "Page limit: 8" invented from its
        own prose reads exactly like "Page limit: 8" read off the CFP, and only one of them
        is true.
        """
        value = getattr(self, field_name, None)
        label = field_name.replace("_", " ")
        if value in (None, "", 0):
            return f"{label}: unknown — do not assume one"
        src = self.sources.get(field_name, "")
        note = {"cfp": " (from the call for papers)",
                "analysis": " (inferred, UNVERIFIED — confirm against the CFP)",
                "author": " (set by the author)"}.get(src, "")
        return f"{label}: {value}{note}"


def venue_slug(name: str) -> str:
    """A short handle for a venue — and the token that will sit in every filename.

    It rides inside the naming chain (`260714_Chords_ismir_outline_ra.docx`), which splits
    on underscores and treats each token as a word, so a slug may contain neither an
    underscore nor a dot. Acronyms survive as themselves ("ISMIR" -> "ismir"); a long name
    collapses to its initials rather than to an unreadable stub.
    """
    import re as _re

    text = (name or "").strip()
    if not text:
        return ""
    # a parenthesised acronym is the handle the field already uses: "… (ISMIR)"
    m = _re.search(r"\(([A-Za-z][A-Za-z0-9&+-]{1,12})\)\s*$", text)
    if m:
        text = m.group(1)
    tokens = [t for t in _re.split(r"[^A-Za-z0-9]+", text) if t]
    if not tokens:
        return ""
    # the venue's OWN acronym beats anything we could construct: "NIME 2027" is nime, not n2
    for t in tokens:
        if t.isupper() and not t.isdigit() and 2 <= len(t) <= 12:
            return t.lower()
    words = [t for t in tokens
             if not t.isdigit()
             and t.lower() not in ("the", "of", "on", "for", "and", "in", "a", "an")]
    if not words:
        return tokens[0][:12].lower()
    if len(words) == 1:
        return words[0][:12].lower()
    return "".join(w[0] for w in words)[:12].lower()


_VENUE_ALIASES = {"venue": "name", "notes": "format_notes", "style": "citation_style"}


def _venue_from(slug: str, data: dict) -> "VenueConfig":
    """One venue entry from yaml, tolerant of the shapes a human writes by hand."""
    raw = {_VENUE_ALIASES.get(k, k): v for k, v in (data or {}).items()}
    raw.pop("slug", None)
    known = {f.name for f in fields(VenueConfig)}
    extra = {k: v for k, v in raw.items() if k not in known}
    clean = {k: v for k, v in raw.items() if k in known}
    if extra:
        # A hand-written key we don't know is worth keeping visible, not silently dropped.
        notes = clean.get("format_notes", "")
        clean["format_notes"] = "\n".join(
            [notes] + [f"{k}: {v}" for k, v in extra.items()]).strip()
    clean.setdefault("name", slug)
    clean["sources"] = dict(clean.get("sources") or {})
    if clean.get("columns") in ("", 0):
        clean["columns"] = None
    return VenueConfig(**clean)


@dataclass
class ZoteroConfig:
    api_key: str = ""
    library_id: str = ""
    library_type: str = "user"

    @classmethod
    def from_env(cls) -> "ZoteroConfig":
        return cls(
            api_key=os.environ.get("ZOTERO_API_KEY", ""),
            library_id=os.environ.get("ZOTERO_LIBRARY_ID", ""),
            library_type=os.environ.get("ZOTERO_LIBRARY_TYPE", "user"),
        )

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.library_id)

    @property
    def have_zotero(self) -> bool:
        return self.available


@dataclass
class ProjectConfig:
    short_title: str = ""
    title: str = ""
    description: str = ""
    topic: str = ""
    focus: str = ""
    litrev_dir: str = ""
    use_methods: bool = False
    results_dir: str = ""
    methods_drafted: bool = False
    results_dir_drafted: bool = False
    style_author: str = ""
    use_style: bool = False
    style_paper_keys: list = field(default_factory=list)
    venues: dict = field(default_factory=dict)      # slug -> VenueConfig
    brain: BrainConfig = field(default_factory=BrainConfig)

    # How the venue's word budget divides across sections — kind -> share, summing to 1.0.
    # Empty means guards.DEFAULT_SECTION_SHARES, which is Results-heavy: a uniform per-
    # subsection allocation gives a section words for having many subsections rather than
    # for having the most to say, and hands Methods 41% of a paper while Results writes the
    # contribution in 18%. Set per project when a paper's balance genuinely differs.
    section_shares: dict = field(default_factory=dict)

    # ── venues ────────────────────────────────────────────────────────────────
    # A venue is a facet of the DELIVERABLE, not of the project: the one-pager is the
    # narrative and belongs to nobody, but an outline and a manuscript are written for a
    # particular venue, and a project may target several (a conference paper, then the
    # journal version that extends it).

    def venue(self, slug: str) -> VenueConfig | None:
        return self.venues.get((slug or "").lower())

    def selected_venues(self) -> list[str]:
        """Slugs the author has chosen to write for, in declaration order."""
        return [s for s, v in self.venues.items()
                if (v.status or "").lower() in ("selected", "submitted", "published")]

    def default_venue(self) -> str | None:
        """The venue a bare `raconteur outline` means.

        Exactly one selected venue is unambiguous. Several is a question only the author
        can answer, so the caller must ask rather than guess: writing the ISMIR paper when
        the author meant the JASSS one wastes a cycle and is not obvious from the output.
        """
        chosen = self.selected_venues()
        return chosen[0] if len(chosen) == 1 else None

    def save(self, project_dir: Path) -> None:
        data = asdict(self)
        path = project_dir / PROJECT_CONFIG_FILE
        path.parent.mkdir(exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    @classmethod
    def load(cls, project_dir: Path) -> "ProjectConfig":
        path = project_dir / PROJECT_CONFIG_FILE
        with open(path) as f:
            data = yaml.safe_load(f)
        data.pop("scope", None)
        data.pop("author_initials", None)
        # backward compat: raster methods moved from a code/ dir to a root file
        if "methods_dir" in data:
            data["use_methods"] = bool(data.pop("methods_dir"))
        if "methods_dir_drafted" in data:
            data["methods_drafted"] = bool(data.pop("methods_dir_drafted"))
        brain_data = data.pop("brain", {})
        # backward compat: old field names
        if "coordinator" in brain_data:
            brain_data["coordinator_model"] = brain_data.pop("coordinator")
        if "worker" in brain_data:
            brain_data["worker_model"] = brain_data.pop("worker")

        venues = {slug.lower(): _venue_from(slug, v)
                  for slug, v in (data.pop("venues", {}) or {}).items()}
        # backward compat: the single `venue:` block every project has today. A project that
        # named one venue meant to write for it, so it loads as a SELECTED venue.
        legacy = data.pop("venue", {}) or {}
        if legacy.get("name") and not venues:
            slug = venue_slug(legacy["name"])
            venues[slug] = _venue_from(slug, {**legacy, "status": "selected"})

        return cls(
            **data,
            brain=BrainConfig(**brain_data),
            venues=venues,
        )

    @classmethod
    def exists(cls, project_dir: Path) -> bool:
        return (project_dir / PROJECT_CONFIG_FILE).exists()


@dataclass
class GlobalConfig:
    ollama_url: str = "http://localhost:11434"
    coordinator_model: str = "qwen3.6:27b-16k"
    worker_model: str = "llama3.1:8b"
    notify_to: str = ""
    mail_prog: str = ""

    @property
    def notify_recipient(self) -> str:
        return self.notify_to

    @classmethod
    def load(cls) -> "GlobalConfig":
        # raconteur's legacy file already uses the unified [ollama]/[notify] shape.
        legacy = haarpi_config.load_toml(GLOBAL_CONFIG_PATH)
        data = haarpi_config.merged_config("raconteur", legacy)
        cfg = cls()
        ollama = data.get("ollama", {})
        cfg.ollama_url = ollama.get("url", cfg.ollama_url)
        cfg.coordinator_model = ollama.get("coordinator", cfg.coordinator_model)
        cfg.worker_model = ollama.get("worker", cfg.worker_model)
        notify = data.get("notify", {})
        cfg.notify_to = notify.get("to", "")
        cfg.mail_prog = notify.get("mail_prog", "")
        cfg.ollama_url = os.environ.get("OLLAMA_URL", cfg.ollama_url)
        cfg.notify_to = os.environ.get("RACONTEUR_NOTIFY_TO", cfg.notify_to)
        cfg.mail_prog = os.environ.get("RACONTEUR_MAIL_PROG", cfg.mail_prog)
        return cfg
