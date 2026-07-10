# haarpi (core)

Shared library and umbrella CLI for the HAARPi pipeline. The stage tools —
[rabbithole](../rabbithole), [raster](../raster), [rayleigh](../rayleigh),
[raconteur](../raconteur) — depend on this package for the machinery they
previously each carried a copy of:

- `haarpi.trundlr` — the one trundlr API client
- `haarpi.notify` — mailer piggybacking on SLURM MailProg / mail / sendmail
- `haarpi.runlog` — unified run logging
- `haarpi.naming` — the document revision naming chain (`260710_title_ra_DCR.docx`)
- `haarpi.render` — markdown → .docx via pandoc

The umbrella CLI provides the cross-stage verbs: `haarpi init`, `status`,
`queue`, `parseNplan`.
