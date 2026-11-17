from directory.source.model import SourceEvidence, InvalidSource, validate_record


class RecordReader:
    def __init__(self, descriptor, records):
        self.descriptor = descriptor
        self._iterator = iter(records)
        self._started = False
        self._closed = False
        self._completed = False
        self.count = 0

    @property
    def completed(self):
        return self._completed

    def __iter__(self):
        if self._started or self._closed:
            raise InvalidSource("reader is not available")
        self._started = True
        while True:
            if self._closed:
                raise InvalidSource("reader closed")
            try:
                record = next(self._iterator)
            except StopIteration:
                self._completed = True
                return
            validate_record(record)
            self.count += 1
            yield record

    def evidence(self):
        if not self.completed:
            raise InvalidSource("source has not reached EOF")
        return SourceEvidence(self.descriptor, self.count, True)

    def close(self):
        self._closed = True
        self._iterator = None
