"""What a reviewer's comment can ask for, per deliverable — ONE table, read two ways.

`haarpi next` sorts every unresolved comment into a kind (``sources``, ``section``, ``fact``…),
and the kind decides the rework that gets queued. The sorting is a model call, steered by the
example phrasings in its prompt. The deliverable's opening instructions tell the reviewer
which phrasings to use. Written separately, those two would drift: the document would promise
a phrasing the classifier no longer recognises. So both are generated from this table — the
classifier prompt by `decompose_prompt`, the reviewer's guide by `phrasing_table`.

Order matters: the classifier decides in table order and stops at the first kind that fits,
so a narrow kind (``correct``) sits before a broad one (``edit``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    name: str
    rule: str                 # the classifier's definition, verbatim into the prompt
    triggers: tuple = ()      # phrasings shown to the reviewer; each must sort to this kind
    does: str = ""            # what the reviewer is told happens
    cost: str = ""            # roughly what it costs, so the reviewer sees it before asking


@dataclass(frozen=True)
class Vocabulary:
    deliverable: str          # how the prompt names the document
    kinds: tuple
    query_rules: str          # which kinds carry a "query" (and any other field)
    example: str              # the JSON shape, as the prompt shows it

    @property
    def names(self) -> tuple:
        return tuple(k.name for k in self.kinds)

    def kind(self, name: str) -> Kind | None:
        return next((k for k in self.kinds if k.name == name), None)


_HEAD = """\
A reviewer left these unresolved annotations on a {deliverable}, numbered:
{{annotations}}

For EACH annotation decide what WORK it needs, then group annotations that need the SAME
work into one task. Every annotation number must appear in exactly one task.
{extra}
Decide in THIS ORDER and stop at the first that fits — a later type never overrides an
earlier one:

"""


def decompose_prompt(vocab: Vocabulary, extra: str = "") -> str:
    """The classifier prompt. `{annotations}` (and any field `extra` introduces) is left for
    the caller to format; literal braces in the JSON example are doubled already."""
    rules = "\n".join(f'{i}. "{k.name}": {k.rule}' for i, k in enumerate(vocab.kinds, 1))
    head = _HEAD.format(deliverable=vocab.deliverable, extra=(f"\n{extra}\n" if extra else ""))
    return (head + rules + "\n\n" + vocab.query_rules + "\n\nRespond ONLY with JSON:\n"
            + vocab.example)


def phrasing_table(vocab: Vocabulary) -> list[str]:
    """The reviewer's guide: one markdown table row per kind that has phrasings to offer."""
    rows = ["| To ask for | Write something like | What happens |",
            "|---|---|---|"]
    for k in vocab.kinds:
        if not k.triggers:
            continue
        said = "; ".join(f"“{t}”" for t in k.triggers)
        does = k.does + (f" *({k.cost})*" if k.cost else "")
        rows.append(f"| **{k.name}** | {said} | {does} |")
    return rows


# ── the narrative literature review (and every review that is not a fact base) ──

LITREVIEW_NARRATIVE = Vocabulary(
    deliverable="literature-review draft",
    kinds=(
        Kind("correct", """the reviewer states that a NAME OR TERM the review uses is factually wrong, and
   says (or clearly implies) the right one — "you mean X, not Y", "that's called X", "get the
   name right, it's X". The work is a substitution, not a rewrite. Give "wrong" (the term as
   the review has it) and "right" (the term it should be). Choose this ONLY for a term the
   review actually uses; a request to discuss a new topic is "section" or "sources".""",
             ("you mean X, not Y", "that's called X"),
             "X replaces Y everywhere: the brief, the config and the draft"),
        Kind("redirect", """the review is aimed wrong or needs a fundamentally different scope — a
   genuine change of direction, not "add more".""",
             ("the review is aimed wrong", "needs a fundamentally different scope"),
             "a new direction: gather on it and re-draft the whole review", "days"),
        Kind("section", """asks for a new section, theme, strand, or sub-topic to be DEVELOPED as its
   own treatment — the review's STRUCTURE must change, not just its wording. Triggers:
   "a section on X", "identify/enumerate/lay out the Xs", "cover the specific techniques
   and how to use them", "this is too high level — I want the concrete X". A demand to
   build out a topic into its own strand is section, NOT sources, even when that topic is
   already mentioned in passing. When in doubt between section and sources, choose section:
   a section is drafted and spliced in without touching the rest of the review, so guessing
   this one wrong costs a strand the reviewer can reject, never a re-read of the document.""",
             ("a section on X", "lay out the Xs"),
             "sources on X gathered, a section drafted and spliced in", "a day"),
        Kind("cite", """the reviewer says the papers are ALREADY in the Zotero library/collection and
   asks to cite them — "I've added Doblinger 2019 and Howell 2017 to Zotero, cite them",
   "these are in the collection now, use them". Nothing is fetched; the papers only need
   embedding so the reviser can cite them. Choose this, NOT ingest, whenever the reviewer
   states the works are already in the library/collection.""",
             ("these are in the collection now, use them",),
             "the papers are embedded and cited"),
        Kind("ingest", """names specific papers, DOIs, authors, or citations the reviewer wants pulled
   in that are NOT yet in the library — pasted reference text, "add @key", "cite Smith 2020",
   "these references:". The works must be fetched before they can be cited.""",
             ("cite Smith 2020", "these references: …"),
             "the papers are fetched into Zotero, then cited"),
        Kind("sources", """wants more evidence UNDER the structure that already exists — thicker support
   for a point the review already makes ("more on X", "go deeper", "what about Y"). Use this
   only when no new section is being asked for; it gathers literature, it does not re-plan.""",
             ("more on X", "what about Y"),
             "more literature gathered for the point", "a day"),
        Kind("edit", """satisfiable by rewriting text that is already there — reword, restructure,
   clarify, cut. No new sources, no new structure.""",
             ("reword", "clarify", "cut"),
             "a tracked rewrite of the sentences commented on"),
    ),
    query_rules="""\
For "redirect", "section", and "sources" give a specific, searchable "query" (the topic to
gather or the section to plan). For "edit", "ingest", and "cite" set "query" to "".
For "correct" set "query" to "" and give "wrong" and "right" instead.""",
    example="""\
{{"tasks": [{{"comments": [1, 2], "need": "sources", "query": "..."}},
            {{"comments": [3], "need": "correct", "wrong": "...", "right": "..."}}]}}""",
)


# ── the fact base (`report_format: facts`) ──────────────────────────────────────
# A fact is the source's, not the tool's: it changes only by re-reading the source, never by
# rewording it to satisfy a comment. So no kind here is a prose edit of a fact; each is a
# deterministic re-run of part of the report.

LITREVIEW_FACTS = Vocabulary(
    deliverable="literature-review fact base (facts extracted from each source, each with "
                "its page and quote, scored against approved topics)",
    kinds=(
        Kind("correct", """the reviewer states that a NAME OR TERM the review's own scaffolding uses (a
   topic name, a research question, the brief) is wrong and says the right one — "you mean X,
   not Y", "that's called X". Give "wrong" and "right". NOT for wording inside a fact: a fact
   is the source's, and a complaint about one is "fact".""",
             ("you mean X, not Y",),
             "the term is replaced in the brief, the config and the topic names — never inside "
             "a fact, which must match its source"),
        Kind("redirect", """the review is aimed wrong or needs a fundamentally different scope — a
   genuine change of direction, not "add more".""",
             ("the review is aimed wrong", "needs a fundamentally different scope"),
             "gather on the new direction; its sources' facts extracted and scored. Change the "
             "topics too if the direction needs them", "a day or more"),
        Kind("topic", """asks for a subject to be covered as a topic of its own — "add a topic on X", "a
   section on X", "X needs its own treatment", "nothing here covers X". Give the topic's name
   as "query", and "serves": the numbers of the research questions it would serve.""",
             ("add a topic on X", "a section on X"),
             "X joins the approved topics and is searched for; every fact is re-scored",
             "about 2.5 h on top of the search"),
        Kind("topic_scope", """says facts are filed under the wrong topic, or that a topic is too broad or
   vague — "this doesn't belong under T7", "T7 should only mean …", "this is calibration, not
   record bias". Give "topic": the topic's number, and "query": one sentence saying what the
   topic covers and what it does not.""",
             ("this doesn't belong under T7", "T7 should only mean …"),
             "the topic gets a written scope that the scorer reads; every fact is re-scored",
             "about 2.5 h"),
        Kind("cite", """the reviewer says the papers are ALREADY in the Zotero library/collection and
   wants them in the review — "I've added Smith 2020 to Zotero". Nothing is fetched.""",
             ("I've added Smith 2020 to Zotero",),
             "the papers are embedded and their facts extracted"),
        Kind("ingest", """names specific papers, DOIs, or citations the reviewer wants pulled in that are
   NOT yet in the library — pasted reference text, "add Smith 2020", "these references:".""",
             ("add Smith 2020", "these references: …"),
             "the papers are fetched into Zotero, embedded, and their facts extracted"),
        Kind("pdf", """says a source's PDF has been fixed or replaced — "fixed the PDF", "OCR'd this",
   "replaced the file", "uploaded a better copy".""",
             ("fixed the PDF", "OCR'd this"),
             "that source's facts are extracted again from the new file — comment on its entry"),
        Kind("fact", """says a fact misstates its source, has the wrong number or page, or misses what the
   paper is actually about — "this isn't what the paper says", "wrong figure", "this misses
   the main finding", "the quote doesn't support this".""",
             ("this isn't what the paper says", "this misses the main finding"),
             "that source's facts are extracted again — comment on the fact itself"),
        Kind("drop", """says a source does not belong in the review — "not relevant", "remove this
   source", "off topic", "this is not the same concept".""",
             ("not relevant", "remove this source"),
             "the source is quarantined from the corpus — comment on its entry (reversible: "
             "`rabbithole collect --status KEY=corpus`)"),
        Kind("sources", """wants more evidence for a subject the topics already cover — "more on X", "thin",
   "what about Y", "go deeper". Give a specific, searchable "query".""",
             ("more on X", "thin", "what about Y"),
             "more literature gathered, and its facts extracted and scored", "a day"),
        Kind("edit", """asks to reword a synthesis bullet or a theme's name — the only text in a fact
   base the tool writes itself.""",
             ("reword",),
             "the theme names and synthesis bullets are written again"),
    ),
    query_rules="""\
For "redirect", "topic", "topic_scope" and "sources" give "query" as described above. For
"topic" also give "serves" (a list of research-question numbers), and for "topic_scope" give
"topic" (a number). For the other kinds set "query" to "". For "correct" give "wrong" and
"right" instead.""",
    example="""\
{{"tasks": [{{"comments": [1], "need": "topic", "query": "...", "serves": [2]}},
            {{"comments": [2, 4], "need": "fact", "query": ""}},
            {{"comments": [3], "need": "topic_scope", "topic": 7, "query": "..."}}]}}""",
)


def litreview_vocabulary(fact_base: bool) -> Vocabulary:
    return LITREVIEW_FACTS if fact_base else LITREVIEW_NARRATIVE


# ── the release guide every deliverable opens with ─────────────────────────────

RELEASE_MEANING = (
    "**What releasing means.** Release this document when it is good enough for the next "
    "stage to build on — not when it is perfect. Releasing passes it on; holding it sends it "
    "back for the rework your comments ask for.")

HOW_TO_RELEASE = (
    "**To release:** resolve every comment, save as `…_DCR.docx` (your initials last), and "
    "run `haarpi next`.  \n"
    "**To hold:** leave each problem as a comment on the passage it concerns, unresolved, "
    "save as `…_DCR.docx`, and run `haarpi next`. Write one ask per comment — every comment "
    "is sorted into one of the kinds below, and the kind decides the rework.")


def release_guide(vocab: Vocabulary, checklist: list[str], release_when: str = "",
                  not_reasons: str = "") -> list[str]:
    """The block a deliverable opens with: what releasing means and how (haarpi's, the same
    for every deliverable), the checklist (the agent's own), and how to ask for rework — the
    phrasing table generated from the same vocabulary the classifier is prompted with."""
    out = ["## Before you release this document", "", RELEASE_MEANING, "",
           "**Check, in order:**", ""]
    out += [f"{i}. {c}" for i, c in enumerate(checklist, 1)]
    out += [""] + ([f"**Release when** {release_when}", ""] if release_when else [])
    out += [HOW_TO_RELEASE, ""]
    if not_reasons:
        out += [f"*Not reasons to hold the release:* {not_reasons}", ""]
    out += ["**How to ask for things:**", ""] + phrasing_table(vocab) + [""]
    return out
