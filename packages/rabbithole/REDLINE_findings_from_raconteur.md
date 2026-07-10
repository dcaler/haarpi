# Findings from folding the redline + guards into raconteur

Guidance for the rabbitHole dev agent, 2026-07. The reciprocal of
`DESIGN_redline_and_guards.md`, which told raconteur to port this machinery.

The port is done: raconteur now has `guards.py`, `redline.py`, and a sentence-indexed
`redline_revise.py`, with 130 tests. Porting the code meant reading it closely and building
fixtures for it, and that turned up **two confirmed bugs that are live in rabbitHole today**,
plus two latent hazards worth closing.

Everything below was reproduced against rabbitHole's own modules, not inferred from raconteur's
copy. Line numbers are rabbitHole's as of this writing.

---

## 1. Sentinel-width off-by-one in `comment_spans` (confirmed, live)

**`rabbithole/redline.py:437`**

```python
offset += len(f"⟦{_sentinel_kind(child)}:0⟧")     # assumes the key is always 5 chars
```

but `serialize_paragraph` (line 230) emits the real index:

```python
key = f"⟦{_sentinel_kind(child)}:{n}⟧"            # ⟦m:1⟧ … ⟦m:10⟧ … ⟦m:12⟧
```

`⟦m:1⟧` is 5 characters. `⟦m:10⟧` is 6. From the **tenth atom in a paragraph onward**,
`comment_spans` under-counts by one character per atom, so every comment anchored after it
resolves to the wrong character range.

### Repro

```python
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree
from rabbithole import redline

MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"

def omath(t):
    om = etree.SubElement(etree.Element("root"), f"{{{MATH}}}oMath")
    x = etree.SubElement(etree.SubElement(om, f"{{{MATH}}}r"), f"{{{MATH}}}t"); x.text = t
    return om

p = Document().add_paragraph("")
for i in range(12):
    p._p.append(omath(f"e{i}"))
p._p.append(redline._text_run("Tail sentence."))

runs = p._p.findall(qn("w:r"))
s = OxmlElement("w:commentRangeStart"); s.set(qn("w:id"), "9")
e = OxmlElement("w:commentRangeEnd");   e.set(qn("w:id"), "9")
runs[0].addprevious(s); runs[0].addnext(e)

text  = redline.paragraph_text(p._p)
spans = redline.comment_spans(p._p)
true_offset = len("".join(f"⟦m:{i}⟧" for i in range(1, 13)))

print(true_offset, spans["9"][0])                          # 63 60  -> 3 chars of drift
print(redline.anchored_sentences(text, spans["9"]))        # {0}    -> should be {1}
```

The comment brackets the tail sentence and `anchored_sentences` reports the *first* one.

### Why it matters

`comment_spans` → `anchored_sentences` → the `anchored` set → `guards.minimal_edit_violation`.
A wrong `anchored` set corrupts the minimality guard in **both** directions:

- A legitimate edit to the sentence the reviewer actually highlighted is rejected as
  collateral damage, and `_redline_para_adversary` fails closed. The comment is silently
  declined and the reviewer is told nothing could be done.
- Worse: the reviser is *licensed* to rewrite a sentence the comment never bore on, because
  that sentence is now inside `anchored`. That is precisely the collateral drift the guard
  exists to prevent, produced by the guard itself.

The trigger is a paragraph with ten or more atoms. A Results-style paragraph full of inline
statistics — the exact case the atom stream was built for — hits it immediately. A narrative
paragraph with many hyperlinks does too, since `_sentinel_kind` numbers `h:` atoms in the same
sequence.

### Fix

Mirror `serialize_paragraph`'s counter. In `comment_spans`:

```python
    offset = 0
    n = 0
    ...
        elif _is_opaque(child):
            n += 1
            offset += len(f"⟦{_sentinel_kind(child)}:{n}⟧")
```

Regression test: assert `comment_spans` returns `len("".join(f"⟦m:{i}⟧" for i in 1..12))`
for a comment placed after twelve atoms. raconteur's is
`tests/test_redline.py::test_comment_spans_width_correct_past_the_tenth_atom`.

---

## 2. Density guards fire on front matter (confirmed, live)

**`rabbithole/guards.py`** — `uncited_paragraphs` and `sparse_paragraphs` iterate every
`Paragraph`, including those before the first `## ` heading, which `parse_paragraphs` assigns
`section = -1`.

### Repro

```python
from rabbithole import guards
md = "# A Review\n\n*Project:* Solar *Sources:* 42\n\n## Background\n\nX [@a].\n"
f = guards.uncited_paragraphs(guards.parse_paragraphs(md))
print([(x.kind, x.where) for x in f])     # [('uncited', 'section -1 para 0')]
```

The metadata block is reported as a paragraph that "cites no source" and must "state the
source(s) for its ideas as [@citekey] tags".

### Why it matters

It is not a crash, it is worse: it is a **false finding fed to the reviser as an imperative**.
The repair loop will dutifully try to attach a citation to `*Project:* Solar *Sources:* 42`,
or burn a round failing to. `by_section` then drops the finding (it filters `section >= 0`),
so the imperative reaches the model but the repair can never be attributed anywhere — the
guard cannot be satisfied and cannot be routed.

I found this by running the guard battery against a **real** rabbitHole output
(`260523_SolarAdopt/litReview/output/SolarAdopt_litreview_ollama.md`), not against a fixture.
`DESIGN_redline_and_guards.md` says to build the guards against a real annotated document
before touching a prompt. That advice paid for itself here: 41 of 44 paragraphs were reported
uncited, and inspecting the first one exposed this.

### Fix

Gate both guards on a body predicate:

```python
def _is_body(p: Paragraph) -> bool:
    return p.section >= 0        # front matter precedes the first "## " heading
```

raconteur additionally exempts an `## Abstract` section, since an abstract summarises rather
than cites. Whether rabbitHole's narratives have one, you would know better than I do.

---

## 3. `w:t`-only prose reads (latent, scoped)

Two readers extract prose by walking `w:t` alone. An equation's characters live in `m:t` on a
sibling `m:oMath`, so both return prose with a hole where every number was. This is the same
class of bug the atom stream was invented to kill — it just survived in the readers that
predate it.

| Location | Used for | Impact today |
|---|---|---|
| `redline.py:502` `_accepted_para_text` | learning the cited `[@citekey]` set after a redline | **Benign.** Citekeys are `w:t`. It would become a real bug the moment this text is used as prose. |
| `docxio.py:71` `read_body_text` | fallback narrative when the matching `.md` is missing (`revise.py:854`) | **Real, narrow.** On that fallback path the resynthesised narrative silently loses every equation. |

raconteur hit the same thing: its `revise.read_text` was `"\n\n".join(p.text for p in ...)`,
and `--resynth` was dropping inline statistics. Demonstrated:

```
OLD: 'Accuracy rose to  overall.'
NEW: 'Accuracy rose to 0.94 overall.'
```

### Fix

Add a flattening reader beside `paragraph_text`, and point both call sites at it:

```python
def atom_text(el) -> str:
    """The visible characters inside an opaque atom — m:t for equations, w:t otherwise."""
    parts  = [t.text or "" for t in el.iter(f"{{{_MATH}}}t")]
    parts += [t.text or "" for t in el.iter(qn("w:t"))]
    return "".join(parts)

def flatten_paragraph(p_el) -> str:
    """Plain prose: atoms rendered as their own text rather than as ⟦m:1⟧ sentinels."""
    text, smap, _ = serialize_paragraph(p_el)
    for key, el in smap.items():
        text = text.replace(key, atom_text(el))
    return text
```

`serialize_paragraph` already drops `w:del` and reads `w:ins` as accepted, so this preserves
`read_body_text`'s documented semantics exactly.

---

## 4. The fail-closed handler eats a mis-scripted test (hazard)

`_redline_para_adversary` wraps every brain call in `except Exception` and returns
`(None, "skipped")`. That is correct in production and dangerous in a test harness.

If a scripted/fake brain signals "you asked for more responses than I have" by raising
anything derived from `Exception` — `AssertionError`, `IndexError`, `StopIteration` — the
adversary swallows it and returns `skipped`. An under-scripted test then **passes**, because
`skipped` is exactly what most of these tests assert. The suite reports green while never
exercising the path it claims to.

I hit this while writing raconteur's `tests/test_revise_adversary.py`: a routing test was
"passing" as a fail-closed skip because the guards had rejected the edit before the audit ran
and the brain ran dry.

### Fix

Make exhaustion escape the net:

```python
class Exhausted(BaseException):
    """Deliberately NOT an Exception — the adversary's except-Exception must not eat it."""
```

Then re-run `tests/test_revise_adversary.py`. If everything still passes, you have learned
that nothing depended on the mask; if something fails, you have found a test that was never
testing what it claimed.

---

## What raconteur diverged on (informational, not a defect here)

Recorded so the two codebases do not drift silently.

- **Section-kind gating.** raconteur gates the citation floor on
  `guards.section_kind(heading)`: a Methods or Results paragraph is grounded in the methods
  writeup and the results files, not in the bibliography, so demanding a `[@citekey]` there is
  a category error. rabbitHole's narrative is uniformly bibliography-grounded, so it needs no
  such gate — but if a rabbitHole review ever grows a section that is not, the guard will
  misfire the way it does on front matter.

- **Title vs heading.** `is_heading_style` returns True for `Title`. raconteur had to add
  `is_title_style`, because using the title as the enclosing section made the abstract belong
  to a section named after the paper. rabbitHole's flat narrative does not care; anything that
  starts attributing paragraphs to sections will.

- **Routing classes.** rabbitHole routes unsatisfiable comments to `table` / `section` /
  `sources`. raconteur uses `section` / `sources` / `evidence` / `figure`, where `evidence`
  means "this asks for a result or method that does not exist — run rayleigh or raster".
  raconteur cannot manufacture evidence; the honest class is what makes the reply honest.

- **Minor-version datestamps.** Unrelated to the redline, but the same bug may exist wherever
  rabbitHole names a minor revision: a minor version must keep the source file's datestamp and
  extend the initials chain. Only a major version starts a new cycle with today's date.
  raconteur's `minor_name` was calling `today()` and silently re-dating every `focus` output.

---

## Suggested order

1. **§1 sentinel width.** Smallest diff, worst consequence — it corrupts the guard that the
   whole minimality argument rests on. Ship it alone with its regression test.
2. **§4 test hazard.** Do this before §2, so the suite stops lying to you while you work.
3. **§2 front-matter gate.** One predicate, two call sites.
4. **§3 flatten reader.** Mechanical; fixes the `read_body_text` fallback and hardens
   `_accepted_para_text` against future reuse.
