#!/usr/bin/env python3
"""
generate_flowchart_grouped.py

Enhanced version of generate_flowchart.py that:
- Groups nodes into Mermaid `subgraph`s by their top-level folder (or 'root' for files in project root)
- Adds simple classDefs and assigns classes to nodes based on file extension (py, js, html, css, other)
- Outputs a Mermaid `.mmd` file ready for mermaid.live or VS Code preview

Usage:
    python generate_flowchart_grouped.py /path/to/project -o trust_flow_grouped.mmd --orientation LR

"""

import os
import re
import argparse
from pathlib import Path
from collections import defaultdict

PY_IMPORT_RE = re.compile(r'^\s*(?:from\s+([.\w]+)\s+import|import\s+([.\w, ]+))')
JS_IMPORT_FROM_RE = re.compile(r'^\s*import\s+(?:.+?\s+from\s+)?[\'\"]([^\'\"]+)[\'\"]')
JS_REQUIRE_RE = re.compile(r'require\(\s*[\'\"]([^\'\"]+)[\'\"]\s*\)')
HTML_SRC_RE = re.compile(r'<\s*script[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
HTML_LINK_RE = re.compile(r'<\s*link[^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)

EXCLUDE_DIRS = {'node_modules', '.git', 'venv', '__pycache__', 'dist', 'build', '.venv', '.pytest_cache'}

EXT_CLASS = {
    '.py': 'pyfile',
    '.js': 'jsfile', '.jsx': 'jsfile', '.ts': 'jsfile', '.tsx': 'jsfile',
    '.html': 'htmlfile', '.htm': 'htmlfile',
    '.css': 'cssfile', '.scss': 'cssfile', '.less': 'cssfile',
}

DEFAULT_EXT_CLASS = 'otherfile'


def is_excluded(path: Path):
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


def find_files(root: Path, exts=('.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.css')):
    for p in root.rglob('*'):
        if p.is_file() and p.suffix.lower() in exts and not is_excluded(p):
            yield p


def read_text_safe(p: Path):
    try:
        return p.read_text(encoding='utf-8')
    except Exception:
        try:
            return p.read_text(encoding='latin-1')
        except Exception:
            return ''


def normalize_node_id(root: Path, file_path: Path):
    rel = file_path.relative_to(root)
    sid = re.sub(r'[^0-9A-Za-z\-_]', '_', str(rel))
    # ensure starts with letter for mermaid
    if not re.match(r'^[A-Za-z]', sid):
        sid = 'f_' + sid
    return sid


def resolve_relative_import(src_file: Path, import_path: str, root: Path):
    if import_path.startswith('./') or import_path.startswith('../') or import_path.startswith('/'):
        candidate = (src_file.parent / import_path).resolve()
        if candidate.is_file():
            return candidate
        for ext in ('.js', '.jsx', '.ts', '.tsx', '.py', '.html', '.htm'):
            if candidate.with_suffix(ext).is_file():
                return candidate.with_suffix(ext)
        if candidate.is_dir():
            for idx in ('index.js', 'index.jsx', 'index.ts', 'index.html', 'index.py'):
                c2 = candidate / idx
                if c2.is_file():
                    return c2
        return None

    if '.' in import_path or import_path.isidentifier():
        parts = import_path.split('.')
        cur = root
        for part in parts:
            cur = cur / part
            if cur.with_suffix('.py').is_file():
                return cur.with_suffix('.py')
        cur = root
        for part in parts:
            cur = cur / part
        if (cur / '__init__.py').is_file():
            return (cur / '__init__.py')
    return None


def extract_refs_from_file(path: Path, root: Path):
    text = read_text_safe(path)
    refs = set()

    if path.suffix == '.py':
        for ln in text.splitlines():
            m = PY_IMPORT_RE.match(ln)
            if not m:
                continue
            module = m.group(1) or m.group(2)
            if not module:
                continue
            module = module.strip()
            for mod in module.split(','):
                mod = mod.strip()
                resolved = resolve_relative_import(path, mod, root)
                if resolved:
                    refs.add(resolved)

    elif path.suffix in ('.js', '.jsx', '.ts', '.tsx'):
        for ln in text.splitlines():
            m = JS_IMPORT_FROM_RE.search(ln)
            if m:
                imp = m.group(1)
                resolved = resolve_relative_import(path, imp, root)
                if resolved:
                    refs.add(resolved)
            for m2 in JS_REQUIRE_RE.finditer(ln):
                imp = m2.group(1)
                resolved = resolve_relative_import(path, imp, root)
                if resolved:
                    refs.add(resolved)

    elif path.suffix in ('.html', '.htm'):
        for m in HTML_SRC_RE.finditer(text):
            imp = m.group(1)
            if imp.startswith('http://') or imp.startswith('https://') or imp.startswith('//'):
                continue
            resolved = resolve_relative_import(path, imp, root)
            if resolved:
                refs.add(resolved)
        for m in HTML_LINK_RE.finditer(text):
            imp = m.group(1)
            if imp.startswith('http://') or imp.startswith('https://') or imp.startswith('//'):
                continue
            resolved = resolve_relative_import(path, imp, root)
            if resolved:
                refs.add(resolved)

    return refs


def top_level_folder(root: Path, file_path: Path):
    rel = file_path.relative_to(root)
    parts = rel.parts
    if len(parts) == 1:
        return 'root'
    return parts[0]


def build_graph(root: Path):
    files = list(find_files(root))
    node_map = {}
    for f in files:
        node_map[f.resolve()] = normalize_node_id(root, f.resolve())

    edges = set()
    for f in files:
        fpath = f.resolve()
        refs = extract_refs_from_file(f, root)
        for r in refs:
            r2 = r.resolve()
            if r2 in node_map:
                edges.add((node_map[fpath], node_map[r2], str(fpath.relative_to(root)), str(r2.relative_to(root))))
    return node_map, edges


def write_mermaid_grouped(root: Path, node_map, edges, outpath: Path, orientation='TD'):
    inv = {v: k for k, v in node_map.items()}

    # group nodes by top-level folder
    groups = defaultdict(list)
    node_ext_class = {}
    for nid, p in inv.items():
        pth = Path(p)
        full = (root / pth).resolve() if not Path(p).is_absolute() else Path(p)
        # but 'p' in inv is absolute in this script
        full = Path(p)
        tl = top_level_folder(root, Path(full))
        groups[tl].append((nid, full))
        ext = full.suffix.lower()
        node_ext_class[nid] = EXT_CLASS.get(ext, DEFAULT_EXT_CLASS)

    lines = []
    lines.append(f"flowchart {orientation}")

    # create subgraphs
    for grp, nodes in sorted(groups.items()):
        lines.append(f"  subgraph {grp}")
        for nid, full in nodes:
            label = str(full)
            label = label.replace('"', '\\"')
            # shorten label for readability
            short = Path(full).as_posix()
            lines.append(f'    {nid}["{short}"]')
            lines.append(f'    class {nid} {node_ext_class[nid]}')
        lines.append('  end')

    # edges
    for a, b, a_rel, b_rel in edges:
        lines.append(f'  {a} --> {b}')

    # classDefs (visual hints)
    lines.append('')
    lines.append('  classDef pyfile fill:#f8f9fa,stroke:#333,stroke-width:1px')
    lines.append('  classDef jsfile fill:#fff7e6,stroke:#333,stroke-width:1px')
    lines.append('  classDef htmlfile fill:#e8f7ff,stroke:#333,stroke-width:1px')
    lines.append('  classDef cssfile fill:#f0f7e6,stroke:#333,stroke-width:1px')
    lines.append('  classDef otherfile fill:#f3f3f3,stroke:#333,stroke-width:1px')

    outpath.write_text("\n".join(lines), encoding='utf-8')
    print(f"Wrote grouped Mermaid flowchart to {outpath} (nodes: {len(inv)}, edges: {len(edges)}, groups: {len(groups)})")


def main():
    p = argparse.ArgumentParser(description="Generate a grouped Mermaid flowchart from a mixed project folder.")
    p.add_argument('root', help="Project root folder")
    p.add_argument('-o', '--output', default='flowchart_grouped.mmd', help="Output mermaid file")
    p.add_argument('--orientation', choices=['TD','LR'], default='TD', help="TD=top-down, LR=left-right")
    args = p.parse_args()

    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print('Invalid project root:', root)
        return

    node_map, edges = build_graph(root)
    write_mermaid_grouped(root, node_map, edges, Path(args.output), orientation=args.orientation)

if __name__ == '__main__':
    main()
