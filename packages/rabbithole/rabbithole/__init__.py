"""rabbitHole — offline-first research assistant.

Pipeline (two commands + one human gate, both commands repeatable):

    rabbitHole init     interactive wizard -> writes litrev.yaml
    rabbitHole gather   machine: discover & curate sources missing from your Zotero collection
    (you download PDFs and add them to the Zotero collection = the human gate)
    rabbitHole report   machine: read the Zotero corpus -> literature review (.md + .docx)
"""

__version__ = "0.1.0"
