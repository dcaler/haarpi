"""The claim-grounding prompts, exactly as bench FP ran them (2026-11-10 to 11-13).

Copied verbatim from devtools/bench (bench_fp, bench_x2, bench_x2b, bench_x3) when the procedure
was adopted: these are the strings the FP numbers were measured with. Change one only with a
new bench run behind it.
"""

KIND_SYS = """You classify one academic paper by what it is. Read the title, abstract and opening.

- "review": its contribution is a synthesis of other work (a literature review, survey,
  meta-analysis, or a framework built mainly from prior studies).
- "empirical": it reports its own data, experiments, measurements or case evidence.
- "theory": it develops a model, argument or formal result of its own.
- "method": it introduces or evaluates a method, tool or technique.

Reply with JSON only: {"kind": "review" | "empirical" | "theory" | "method", "reason": "one sentence"}"""

CARD_SYS = """You read one academic paper and state what it establishes: its contributions. A
contribution is something this paper itself puts forward and could be cited for: a finding, a
result, a model or method it introduces, a framework, or an argument it makes.

NOT contributions: background; definitions or concepts taken from other work; findings the paper
cites from others (including the authors' own earlier papers); the paper's aims or plans;
settings, parameters or software versions; a single statistic on its own.

First decide how many distinct contributions the paper makes. Do not split one finding into
several, and do not pad. A short paper may make one.

For each contribution give:
- "claim": one or two sentences that a reader could understand with nothing else in front of
  them. Name the subject explicitly (never "this study", "the model", "we"). State the
  contribution itself: never open with "The paper / study / article / authors (proposes, shows,
  suggests, finds)..."; begin with what it is about. Write into the
  sentence the system, population or setting the paper studied, and any conditions it states
  ("in SMEs in the nanomaterials field", "in NK simulations with K above 2"). Keep the paper's
  strength: if it "suggests", say "suggests". State it at the reach the paper shows it: no wider,
  no narrower.
- "quotes": 1 to 3 passages copied exactly from the text (8 to 40 words each) that support it,
  each with its page number from the [p.N] markers.

Reply with JSON only:
{"count": N, "contributions": [{"claim": "", "quotes": [{"text": "", "page": 0}]}]}"""

MERGE_SYS = """You are given the candidate contributions found in each part of one academic
paper, and its abstract. Write the paper's card: merge candidates that are the same contribution,
drop any that are background, cited findings, aims or settings, and keep the rest. Do not invent:
every claim must come from the candidates (keep each claim self-contained, with its setting,
conditions and strength), and every quote must be copied exactly from a candidate's quotes, with
its page. Then state how many distinct contributions the paper makes.

Reply with JSON only, in the same form as the candidates:
{"count": N, "contributions": [{"claim": "", "quotes": [{"text": "", "page": 0}]}]}"""

WINDOW_NOTE = """

This is part {n} of {total} of the paper. List the contributions evident in this part. Another
pass will merge the parts, so do not worry about the whole paper; the count is for this part."""

REREAD_SYS = """You check one statement against the paper it claims to come from. Read the pages
given. Answer strictly: "cant_tell" is allowed and counts against the statement.

- "supported": do the pages support the claim as stated?
- "scope": does the claim keep the reach the paper shows ("matches"), claim more ("overreaches")
  or less ("underreaches")? Check the setting and conditions the claim states too: a claim set
  in a system, population or field the paper did not study overreaches.
- "span": the passage that best supports it, copied EXACTLY as printed, word for word, with its
  citations, figure references and typos. If the support is in two places, copy each exactly and
  join them with " ... ". Never reword, shorten inside a passage, or correct the text.

Reply with JSON only:
{"supported": "yes" | "no" | "cant_tell",
 "scope": "matches" | "overreaches" | "underreaches" | "cant_tell",
 "span": "",
 "reason": "one sentence"}"""

CONFIRM_SYS = """You check whether a claim is said by the passages quoted under it. The passages
are copied from the paper itself. Judge the claim against the passages only (the setting aside): not from what you
know, and not from what the paper might say elsewhere. The title and abstract are given so you know
what the paper's names and terms refer to, and for ONE thing more: the system, population or
setting the paper as a whole studied. If the claim names that setting and the title or abstract
states it for the whole paper, count the setting as given. Everything else in the claim (what was
found, its direction, numbers, conditions) must come from the passages.
Answer strictly: "cant_tell" counts against.

- "says": do the passages, taken together, state this claim? "yes" if a reader of the passages
  alone, knowing the paper's setting, would reach the claim, or a narrower version of it; "no" if the claim adds something the
  passages do not say, or says something else.
- "direction": does the claim run the same way as the passages (an increase is an increase, a
  benefit a benefit)? "same", "opposite", or "n/a" when there is no direction.
- "numbers": do the claim's numbers appear in the passages as stated? "agree", "disagree",
  or "none" when the claim has none.

Reply with JSON only:
{"says": "yes" | "no" | "cant_tell",
 "direction": "same" | "opposite" | "n/a" | "cant_tell",
 "numbers": "agree" | "disagree" | "none" | "cant_tell",
 "reason": "one sentence"}"""

OWN_SYS_REVIEW = """You decide one thing: is a claim this paper's OWN contribution, or something the paper
takes from other work? Use the paper's identity and the passages (copied from the paper itself,
with the sentences around them). Do not judge whether the claim is true or well supported.

This paper is a REVIEW. Its own contributions are its synthesis: patterns it finds
across the studies it reviews, comparisons between them, its framework or classification, its
conclusions and its agenda. A single cited study's result restated as if it were the review's is
NOT its own. A review's comparison or analysis of the studies it
collected ("comparing these landscapes shows...", "a comparative analysis of the data sets...")
is its own synthesis, even though the data come from those studies.

Always:
- Background, definitions and frameworks the paper takes from others are not its own, even when
  it uses them.
- If the passages attribute the point to a citation (other authors, or the authors' own earlier
  work), it is not this paper's own.


If it is the paper's own, also say its role:
- "finding": a result the paper obtains from its own data, analysis, simulation or proof.
- "proposal": a concept, framework, model, method or argument the paper puts forward.
- "synthesis": a review's own conclusion drawn across the studies it collected.

Reply with JSON only:
{"own_finding": "yes" | "no" | "cant_tell",
 "role": "finding" | "proposal" | "synthesis" | "" (empty unless own_finding is yes),
 "source": "for no: who it comes from, as cited, e.g. Kauffman 1989; for yes: empty",
 "reason": "one sentence"}"""

OWN_SYS_NOT_REVIEW = """You decide one thing: is a claim this paper's OWN contribution, or something the paper
takes from other work? Use the paper's identity and the passages (copied from the paper itself,
with the sentences around them). Do not judge whether the claim is true or well supported.

Its own contributions are what it finds, shows, models, proposes or argues. A
result it reports from another work is NOT its own, even when the paper agrees with it.

Always:
- Background, definitions and frameworks the paper takes from others are not its own, even when
  it uses them.
- If the passages attribute the point to a citation (other authors, or the authors' own earlier
  work), it is not this paper's own.


If it is the paper's own, also say its role:
- "finding": a result the paper obtains from its own data, analysis, simulation or proof.
- "proposal": a concept, framework, model, method or argument the paper puts forward.
- "synthesis": a review's own conclusion drawn across the studies it collected.

Reply with JSON only:
{"own_finding": "yes" | "no" | "cant_tell",
 "role": "finding" | "proposal" | "synthesis" | "" (empty unless own_finding is yes),
 "source": "for no: who it comes from, as cited, e.g. Kauffman 1989; for yes: empty",
 "reason": "one sentence"}"""
