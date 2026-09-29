"""One module per feature that has a data page.

Each module here exports the four things the registry wants — an id, a title, a
one-line summary, and ``load(session, *, days)`` — and owns the shape of its own
report. Two features will disagree about what a number is called and about which
numbers matter at all; that disagreement is the point of the design, so nothing
here is shared except ``feature_stats.stats`` and ``feature_stats.pricing``,
which are arithmetic rather than vocabulary.
"""
