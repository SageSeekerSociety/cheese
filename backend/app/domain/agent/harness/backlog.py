"""Read every harness's retained events without mixing their native cursors."""

from dataclasses import replace

from app.domain.agent.harness import Backlog, HarnessEvent


class CombinedBacklog:
    def __init__(self, readers: list[Backlog]):
        self.readers = readers
        self.entries = {
            f"{index}:{entry.key}": (index, entry)
            for index, reader in enumerate(readers)
            for entry in reader.unread()
        }

    def unread(self) -> list[HarnessEvent]:
        return [replace(entry, key=key) for key, (_, entry) in self.entries.items()]

    def assemble(self, entry: HarnessEvent):
        index, original = self.entries[entry.key]
        return self.readers[index].assemble(original)

    def unfinished(self) -> set[str]:
        return {key for reader in self.readers for key in reader.unfinished()}

    def give_up(self):
        return [message for reader in self.readers for message in reader.give_up()]

    def landed(self, *, through: str) -> None:
        cursors = {}
        for key, (index, entry) in self.entries.items():
            cursors[index] = entry.key
            if key == through:
                break
        for index, cursor in cursors.items():
            self.readers[index].landed(through=cursor)

    def forget(self, *, older_than_s: float) -> None:
        for reader in self.readers:
            reader.forget(older_than_s=older_than_s)
