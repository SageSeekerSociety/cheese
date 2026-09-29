"""功能数据: one admin page per feature, each showing what that feature costs.

An admin area with its own shape, next to the feedback queue, the dashboard and

An admin area with its own shape, next to the feedback queue, the dashboard and
the model ledger. Two pages, not one: a **catalogue** that lists features and
carries no numbers at all, and **one page per feature** with the numbers that
feature is judged by. The catalogue deliberately shows nothing — a grid of
figures for features you are not looking at is a page nobody reads, and the one
question the catalogue answers is 「平台上有哪些功能在被量着，分别去哪看」.

The pieces:

* ``registry`` — the list. One ``Feature`` per page: an id, a title, a
  one-line summary, and the function that loads the report. Adding a page
  means adding an entry and the module it points at (see
  ``docs/manual/dev/feature-stats``).
* ``features/`` — one module per feature, holding its own numeric shape. The
  layout of a feature's page is the feature's business, so the loader returns
  whatever that page needs; there is no shared block vocabulary to conform to.
* ``stats`` — the arithmetic several features need (percentiles, histogram
  bins), pure functions over plain lists.
* ``pricing`` — what a feature's tokens would cost, priced at the gateway's
  own rates. An estimate, and labelled as one on the page.

What this domain does **not** do: general-purpose instrumentation, feature
flags, rollout decisions. A feature earns a page here when its owner needs to
defend or kill it, and the numbers are already being written by the feature
itself. Anything that has to add tracking to every request is a different
project; ``docs/manual/dev/feature-stats`` says so where a future reader will
look for it.
"""
