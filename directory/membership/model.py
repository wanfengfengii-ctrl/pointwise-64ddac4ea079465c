from dataclasses import dataclass


class ProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class DepartmentNode:
    path: tuple
    parent: tuple | None
    children: tuple
    direct_dns: tuple
    recursive_dns: tuple

    @property
    def direct_count(self):
        return len(self.direct_dns)

    @property
    def recursive_count(self):
        return len(self.recursive_dns)


@dataclass(frozen=True)
class DepartmentProjection:
    nodes: tuple

    def find(self, path):
        return next((node for node in self.nodes if node.path == path), None)
