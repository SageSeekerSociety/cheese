## Consumer verification boundary

The unchanged auth-fixed run passed 10 cases before the obsolete execute lock hook timed out. The reviewed scalar hook at 2c21dc5ac passed all three original real PostgreSQL answer races in 17.17 seconds. The lock observer still requires PostgreSQL to report B blocked by A before A can commit.

These cases protect retained non-group single-answer compatibility, not atomic group settlement or native Ask creation. Earlier accepted cases were not rerun. The runtime owner closure change added after this run has no native behavior verdict yet.
