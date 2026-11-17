from directory.source.model import SourceDescriptor, InvalidSource
from directory.source.reader import RecordReader


class SourceSession:
    def __init__(self, source_id, records, input_format="records"):
        self.descriptor = SourceDescriptor(source_id, input_format)
        self.reader = RecordReader(self.descriptor, records)
        self._closed = False

    def records(self):
        if self._closed:
            raise InvalidSource("session closed")
        return iter(self.reader)

    def finish(self):
        if self._closed:
            raise InvalidSource("session closed")
        return self.reader.evidence()

    def close(self):
        if not self._closed:
            self.reader.close()
            self._closed = True
