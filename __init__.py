# ----- SECTION: Imports -----
import asyncio
import importlib.util
import inspect
import os
import sys
from typing import Any, Awaitable, Callable, List, Optional

# ----- SECTION: Public Globals -----
WEB_DIRECTORY = "./js"

# ----- SECTION: Module Discovery -----
def _discover_py_files() -> List[str]:
    pkg_dir = os.path.dirname(__file__)
    files: List[str] = []
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        if fname == "__init__.py":
            continue
        if fname.startswith("_"):
            continue
        files.append(os.path.join(pkg_dir, fname))
    files.sort()
    return files


def _safe_mod_name(i: int, path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    pkg = os.path.basename(os.path.dirname(__file__))
    pkg = "".join(c if (c.isalnum() or c == "_") else "_" for c in pkg)
    base = "".join(c if (c.isalnum() or c == "_") else "_" for c in base)
    return f"_v3_{pkg}_{base}_{i}"


def _load_modules(py_files: List[str]) -> List[Any]:
    modules: List[Any] = []
    for i, fpath in enumerate(py_files):
        try:
            print(f"[custom_nodes] Loading module: {fpath}")
            mod_name = _safe_mod_name(i, fpath)
            spec = importlib.util.spec_from_file_location(mod_name, fpath)
            if spec is None or spec.loader is None:
                print(f"[custom_nodes] Could not create spec for {fpath}")
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            modules.append(mod)
            print(f"[custom_nodes] Successfully loaded module: {fpath}")
            print(f"[custom_nodes] Has comfy_entrypoint: {hasattr(mod, 'comfy_entrypoint')}")
            if hasattr(mod, 'comfy_entrypoint'):
                print(f"[custom_nodes] comfy_entrypoint is callable: {callable(mod.comfy_entrypoint)}")
        except Exception as e:
            print(f"[custom_nodes] Import failed for {fpath}: {e}")
    return modules


_MODULES = _load_modules(_discover_py_files())
_ENTRYPOINTS: List[Callable[[], Any]] = []

print(f"[custom_nodes] Total modules loaded: {len(_MODULES)}")
for i, _m in enumerate(_MODULES):
    print(f"[custom_nodes] Processing module {i}: {_m.__name__}")
    _ep = getattr(_m, "comfy_entrypoint", None)
    print(f"[custom_nodes] Has comfy_entrypoint: {_ep is not None}")
    if callable(_ep):
        print(f"[custom_nodes] Adding comfy_entrypoint to list")
        _ENTRYPOINTS.append(_ep)

print(f"[custom_nodes] Total entrypoints collected: {len(_ENTRYPOINTS)}")

# ----- SECTION: Await Helper -----
async def _await_if_needed(v: Any) -> Any:
    if asyncio.iscoroutine(v) or isinstance(v, Awaitable):
        return await v
    return v

# ----- SECTION: V3 Entry Point (comfy_entrypoint) -----
async def comfy_entrypoint():
    print("[__init__] comfy_entrypoint called")
    try:
        from comfy_api.latest import ComfyExtension, io as comfy_io
        print("[__init__] Successfully imported comfy_api")

        class _AggregateExtension(ComfyExtension):
            async def get_node_list(self) -> list[type[comfy_io.ComfyNode]]:
                print("[__init__] get_node_list called")
                nodes: List[type[comfy_io.ComfyNode]] = []
                print(f"[__init__] Processing {len(_ENTRYPOINTS)} entrypoints")
                for i, ep in enumerate(_ENTRYPOINTS):
                    print(f"[__init__] Processing entrypoint {i}")
                    try:
                        ext = await _await_if_needed(ep())
                        print(f"[__init__] Got extension: {ext}")
                        if ext is None:
                            print(f"[__init__] Extension {i} is None")
                            continue
                        get_list = getattr(ext, "get_node_list", None)
                        print(f"[__init__] Got get_node_list: {get_list}")
                        if not callable(get_list):
                            print(f"[__init__] get_node_list is not callable")
                            continue
                        lst = await _await_if_needed(get_list())
                        print(f"[__init__] Got node list: {lst}")
                        if isinstance(lst, list):
                            print(f"[__init__] Adding {len(lst)} nodes")
                            for n in lst:
                                if n not in nodes:
                                    nodes.append(n)
                    except Exception as e:
                        print(f"[__init__] Error processing entrypoint {i}: {e}")
                        import traceback
                        traceback.print_exc()
                print(f"[__init__] Total nodes collected: {len(nodes)}")
                return nodes

        print("[__init__] Successfully created aggregate extension")
        return _AggregateExtension()
    except Exception as e:
        print(f"[__init__] Error in comfy_entrypoint: {e}")
        import traceback
        traceback.print_exc()
        raise

# ----- SECTION: Exports -----
__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
