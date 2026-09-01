#!/usr/bin/env python3
"""The information-flow map: what each release feeds in the paper.

Note what is ABSENT: the preregistration. raconteur.context loads the literature review, the
methods writeup and the results digest, and nothing else — matching
`project.DEFAULT_STAGES["paper"]["inputs"]`. The old hand-drawn map showed the prereg feeding
Methods and Discussion; the drift check caught that on its first run. Whether the paper SHOULD
read its own preregistration is a pipeline question, not a drawing one.

Sources are the stages' MINTED releases plus two produced components. Which stages may
appear is not a free choice — `project.STAGES["paper"]["inputs"]` declares what the paper
stage is allowed to read, and test_figure_drift.py checks this list against it.
"""

# every source, and the stage whose release it is (None = a produced component, not a mint)
FROM_STAGE = {
 "s_one": "paper", "s_skel": "paper", "s_out": "paper",
 "s_lit": "litreview", "s_meth": "build",
 "s_res": "experiments", "s_fig": None, "s_refs": None,
}

SOURCES = {
 "s_one":  ("the one-pager — the narrative through-line", "mint"),
 "s_skel": ("the skeleton — sections, and the words each can afford", "mint"),
 "s_out":  ("the outline — the content beats on the approved skeleton", "mint"),
 "s_lit":  ("the literature review — load-bearing sources, narrative, "
            "annotated bibliography", "mint"),
 "s_meth": ("the methods digest — DESIGN.md + tasks.yaml + the frozen tests", "mint"),
 "s_fig":  ("the figures pool — framework, module graph, data figures", "art"),
 "s_res":  ("the results write-up — the findings, as preregistered", "mint"),
 "s_refs": ("refs.bib — the whole curated corpus", "art"),
}
SECTIONS = {
 "p_abs": ("Abstract", "indigo"), "p_int": ("Introduction", "indigo"),
 "p_bg":  ("Background", "indigo"), "p_meth": ("Methods", "indigo"),
 "p_res": ("Results", "indigo"), "p_dis": ("Discussion", "indigo"),
 "p_con": ("Conclusion", "indigo"),
 "p_ref": ("References — the CITED subset only, via citeproc", "indigo"),
}
# (source, section, kind, label). prose = the section is WRITTEN from this;
# asset = it supplies a citation or a figure placed there.
EDGES = [
 ("s_out",  "p_bg",   "prose", "defines the section structure"),
 ("s_lit",  "p_int",  "prose", "motivation"),
 ("s_lit",  "p_bg",   "prose", "the background pillars"),
 ("s_lit",  "p_dis",  "prose", "vs. prior work"),
 ("s_meth", "p_meth", "prose", "what the code does"),
 ("s_res",  "p_res",  "prose", "the findings"),
 ("s_res",  "p_dis",  "prose", "interpretation"),
 ("s_fig",  "p_meth", "asset", "framework + module figures"),
 ("s_fig",  "p_res",  "asset", "data figures"),
 ("s_refs", "p_bg",   "asset", "grounds the [@key] citations"),
 ("s_refs", "p_ref",  "asset", "citeproc builds the list"),
]
# written LAST, summarising the finished body
DIGESTS = [("p_int", "p_abs"), ("p_meth", "p_abs"), ("p_res", "p_abs"),
           ("p_dis", "p_abs"), ("p_con", "p_abs")]
STRUCTURE = [("s_one", "s_skel", "the through-line"),
             ("s_skel", "s_out", "the approved structure")]
ENCLOSURE = "the manuscript .docx"   # short enough to sit in the folder tab
TITLE = "Information-flow map — what each release feeds in the paper"
