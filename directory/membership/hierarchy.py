def ancestor_paths(path):
    return tuple(path[:size] for size in range(1, len(path) + 1))


def is_ancestor(parent, child):
    return bool(parent) and len(parent) <= len(child) and child[:len(parent)] == parent


def build_hierarchy(entries):
    paths = set()
    for entry in entries:
        if entry.department_path is not None:
            paths.update(ancestor_paths(entry.department_path))
    children = {path: [] for path in sorted(paths)}
    for path in sorted(paths):
        if len(path) > 1:
            children[path[:-1]].append(path)
    return {path: tuple(value) for path, value in children.items()}
