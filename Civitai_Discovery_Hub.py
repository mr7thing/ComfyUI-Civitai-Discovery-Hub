# ----- SECTION: Imports -----
import asyncio
import concurrent.futures
import io
import json
import os
import time
from typing import Any, Dict, List, Optional

import aiohttp
import numpy as np
import torch
from aiohttp import web
from PIL import Image
from server import PromptServer
from typing_extensions import override

from comfy_api.latest import ComfyExtension, io as comfy_io


# ----- SECTION: Constants -----
NODE_DIR = os.path.dirname(os.path.abspath(__file__))
FAVORITES_FILE = os.path.join(NODE_DIR, "civitai_favorites.json")
CONFIG_FILE = os.path.join(NODE_DIR, "config.json")


# ----- SECTION: Config Management -----
# 配置缓存状态
_config_cache: Optional[Dict[str, Any]] = None
_config_mtime: float = 0


def load_config(force_reload: bool = False) -> Dict[str, Any]:
    """Load configuration from config.json file with caching and hot-reload support"""
    global _config_cache, _config_mtime
    
    # 检查是否需要重新加载
    if not force_reload and _config_cache is not None:
        try:
            current_mtime = os.path.getmtime(CONFIG_FILE)
            if current_mtime == _config_mtime:
                return _config_cache
        except OSError:
            pass
    
    # 重新加载配置
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            _config_mtime = os.path.getmtime(CONFIG_FILE)
            print(f"CivitaiDiscoveryHub: Config loaded from {CONFIG_FILE}")
        except Exception as e:
            print(f"CivitaiDiscoveryHub: Error loading config: {e}")
    
    _config_cache = config if isinstance(config, dict) else {}
    return _config_cache


def reload_config() -> Dict[str, Any]:
    """Force reload configuration from disk"""
    return load_config(force_reload=True)


def get_civitai_api_key() -> Optional[str]:
    """Get Civitai API key from environment variable or config file"""
    # First try environment variable
    api_key = os.environ.get("CIVITAI_API_KEY")
    if api_key:
        return api_key.strip()
    
    # Then try config file
    config = load_config()
    api_key = config.get("api_key")
    if api_key:
        return str(api_key).strip()
    
    return None


def get_proxy_settings() -> Dict[str, Any]:
    """Get proxy settings from config file"""
    config = load_config()
    proxy_settings = config.get("proxy", {})
    if not isinstance(proxy_settings, dict):
        proxy_settings = {}
    
    return {
        "enabled": bool(proxy_settings.get("enabled", False)),
        "type": str(proxy_settings.get("type", "http")).lower(),
        "host": str(proxy_settings.get("host", "127.0.0.1")),
        "port": int(proxy_settings.get("port", 10808)),
        "username": str(proxy_settings.get("username", "")) if proxy_settings.get("username") else None,
        "password": str(proxy_settings.get("password", "")) if proxy_settings.get("password") else None,
    }


def get_proxy_url() -> Optional[str]:
    """Get proxy URL string, returns None if proxy is disabled"""
    settings = get_proxy_settings()
    if not settings["enabled"]:
        return None
    
    auth = ""
    if settings["username"] and settings["password"]:
        auth = f"{settings['username']}:{settings['password']}@"
    
    return f"{settings['type']}://{auth}{settings['host']}:{settings['port']}"


# ----- SECTION: Unified HTTP Client -----
class CivitaiHttpClient:
    """
    统一管理的 HTTP 客户端，支持代理热切换
    
    设计原则：
    - 单例模式：全局唯一实例
    - 自动代理检测：配置变化时自动重建 session
    - 连接池复用：减少 TCP 握手开销
    """
    
    _instance: Optional["CivitaiHttpClient"] = None
    _lock: asyncio.Lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._session: Optional[aiohttp.ClientSession] = None
            cls._instance._current_proxy: Optional[str] = None
            cls._instance._closed = False
        return cls._instance
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """获取带 API Key 的请求头"""
        headers = {"User-Agent": "Mozilla/5.0"}
        api_key = get_civitai_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers
    
    async def get_session(self) -> aiohttp.ClientSession:
        """获取或创建 session，自动检测代理变化并重建"""
        async with self._lock:
            if self._closed:
                raise RuntimeError("HttpClient has been closed")
            
            proxy_url = get_proxy_url()
            
            # 代理配置变化时需要重建 session
            need_rebuild = (
                proxy_url != self._current_proxy
                or self._session is None
                or self._session.closed
            )
            
            if need_rebuild:
                # 关闭旧 session
                if self._session and not self._session.closed:
                    await self._session.close()
                
                # 创建新 connector
                if proxy_url:
                    connector = aiohttp.TCPConnector(
                        proxy=proxy_url,
                        limit=100,
                        limit_per_host=30,
                        ttl_dns_cache=300,
                    )
                    print(f"CivitaiDiscoveryHub: HTTP client using proxy {proxy_url.split('@')[-1]}")
                else:
                    connector = aiohttp.TCPConnector(
                        limit=100,
                        limit_per_host=30,
                        ttl_dns_cache=300,
                    )
                    print("CivitaiDiscoveryHub: HTTP client using direct connection")
                
                # 创建新 session
                timeout = aiohttp.ClientTimeout(total=60, connect=10)
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    headers=self._get_auth_headers(),
                )
                self._current_proxy = proxy_url
            
            return self._session
    
    async def request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> aiohttp.ClientResponse:
        """发送 HTTP 请求"""
        session = await self.get_session()
        return await session.request(method, url, **kwargs)
    
    async def get(self, url: str, **kwargs) -> aiohttp.ClientResponse:
        """发送 GET 请求"""
        return await self.request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs) -> aiohttp.ClientResponse:
        """发送 POST 请求"""
        return await self.request("POST", url, **kwargs)
    
    async def close(self):
        """关闭客户端，释放资源"""
        async with self._lock:
            self._closed = True
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
            self._current_proxy = None
    
    async def reload_proxy(self) -> Dict[str, Any]:
        """强制重新加载代理配置并重建 session"""
        reload_config()
        async with self._lock:
            self._current_proxy = None  # 强制触发重建
        session = await self.get_session()
        return {
            "status": "success",
            "proxy": self._current_proxy.split("@")[-1] if self._current_proxy else None,
            "enabled": self._current_proxy is not None,
        }


# 全局 HTTP 客户端实例
http_client = CivitaiHttpClient()


# ----- SECTION: Favorites Storage -----
def load_favorites() -> Dict[str, Any]:
    if not os.path.exists(FAVORITES_FILE):
        return {}
    try:
        with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_favorites(data: Dict[str, Any]) -> None:
    try:
        tmp = FAVORITES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, FAVORITES_FILE)
    except Exception as e:
        print(f"CivitaiDiscoveryHub: Error saving favorites: {e}")


# ----- SECTION: Utils -----
def clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(str(v))
    except Exception:
        return default
    return max(lo, min(hi, n))


def truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def item_is_video(it: Dict[str, Any]) -> bool:
    u = str(it.get("url") or "").lower()
    if u.endswith(".mp4") or u.endswith(".webm"):
        return True
    m = it.get("meta") or {}
    mv = str(m.get("video") or m.get("videoUrl") or m.get("mp4") or m.get("mp4Url") or "").lower()
    return mv.endswith(".mp4") or mv.endswith(".webm")


def item_has_positive_prompt(it: Dict[str, Any]) -> bool:
    m = it.get("meta") or {}
    for k in ("prompt", "Prompt", "positive", "textPrompt"):
        if str(m.get(k) or "").strip():
            return True
    return False


def item_matches_query_local(it: Dict[str, Any], q: str) -> bool:
    if not q:
        return True
    q = q.lower().strip()
    m = it.get("meta") or {}
    buf = " | ".join(
        str(x or "")
        for x in [
            it.get("id"),
            it.get("url"),
            m.get("prompt"),
            m.get("Prompt"),
            m.get("textPrompt"),
            m.get("negativePrompt"),
            m.get("NegativePrompt"),
            (it.get("user") or {}).get("username") or (it.get("user") or {}).get("name") or "",
            m.get("Model") or m.get("model") or "",
        ]
    ).lower()
    return q in buf


def _empty_image_tensor() -> torch.Tensor:
    return torch.zeros(1, 1, 1, 3, dtype=torch.float32)


def _download_image_to_tensor(url: str, timeout_s: int = 30) -> torch.Tensor:
    """
    同步下载图片并转换为 tensor
    
    使用缓存的代理配置，在 ComfyUI 同步环境中运行。
    """
    import urllib.request
    
    headers = {"User-Agent": "Mozilla/5.0"}
    api_key = get_civitai_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    proxy_url = get_proxy_url()
    proxy_handler = None
    
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    
    opener = urllib.request.build_opener(proxy_handler) if proxy_handler else urllib.request.build_opener()
    req = urllib.request.Request(url, headers=headers)
    
    with opener.open(req, timeout=timeout_s) as resp:
        data = resp.read()
    
    img = Image.open(io.BytesIO(data))
    if getattr(img, "is_animated", False):
        try:
            img.seek(0)
        except Exception:
            pass
    img = img.convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


def extract_prompts_from_workflow(workflow_json: str) -> tuple[str, str]:
    """Extract prompts from workflow by tracing CLIPTextEncode nodes' text inputs"""
    try:
        # Parse workflow JSON
        workflow_data = json.loads(workflow_json)
    except json.JSONDecodeError:
        return "", ""
    
    # Get nodes from workflow
    nodes = workflow_data.get("workflow", {}).get("nodes", [])
    if not nodes:
        return "", ""
    
    # Create node dictionary for quick lookup
    node_dict = {str(node.get("id")): node for node in nodes}
    
    # Function to trace text input recursively
    def trace_text_input(node_id: str, visited: set) -> str:
        if node_id in visited:
            return ""
        visited.add(node_id)
        
        node = node_dict.get(node_id)
        if not node:
            return ""
        
        node_type = node.get("type", "")
        widgets_values = node.get("widgets_values", [])
        
        # Check if this is a text display node
        if "ShowText" in node_type or "show text" in node_type.lower():
            # Extract text from widgets_values
            for value in widgets_values:
                if isinstance(value, str):
                    return value
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            return item
        
        # Check if this node has text input widgets
        inputs = node.get("inputs", [])
        for input_info in inputs:
            if input_info.get("name") in ["text", "string"]:
                # Check if it has a widget with text
                widget = input_info.get("widget")
                if widget:
                    # For simple text inputs
                    if isinstance(widgets_values, list) and widgets_values:
                        for value in widgets_values:
                            if isinstance(value, str):
                                return value
                # Check if it links to another node
                                link = input_info.get("link")
                                if link is not None:
                                    # Find the node that this link comes from
                                    for n in nodes:
                                        outputs = n.get("outputs", [])
                                        for output in outputs:
                                            output_links = output.get("links", [])
                                            if output_links and link in output_links:
                                                # Trace to the source node
                                                source_node_id = str(n.get("id"))
                                                result = trace_text_input(source_node_id, visited)
                                                if result:
                                                    return result
        
        # Check if this node has any text-like widgets
        for value in widgets_values:
            if isinstance(value, str) and len(value) > 10:
                return value
        
        return ""
    
    # Find CLIPTextEncode nodes
    positive_prompt = ""
    negative_prompt = ""
    
    for node in nodes:
        node_type = node.get("type", "")
        if node_type == "CLIPTextEncode":
            node_title = node.get("title", "")
            inputs = node.get("inputs", [])
            
            # Find text input
            for input_info in inputs:
                if input_info.get("name") == "text":
                    link = input_info.get("link")
                    if link is not None:
                        # Find the node that this link comes from
                        for n in nodes:
                            outputs = n.get("outputs", [])
                            for output in outputs:
                                output_links = output.get("links", [])
                                if output_links and link in output_links:
                                    source_node_id = str(n.get("id"))
                                    text = trace_text_input(source_node_id, set())
                                    if text:
                                        if "negative" in node_title.lower():
                                            negative_prompt = text
                                        else:
                                            positive_prompt = text
                    else:
                        # Check if it has a widget with text
                        widget = input_info.get("widget")
                        if widget:
                            widgets_values = node.get("widgets_values", [])
                            for value in widgets_values:
                                if isinstance(value, str):
                                    if "negative" in node_title.lower():
                                        negative_prompt = value
                                    else:
                                        positive_prompt = value
    
    return positive_prompt, negative_prompt


# ----- SECTION: Node (V3) -----
class CivitaiDiscoveryHubNode(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="CivitaiDiscoveryHubNode",
            display_name="🖼️ Civitai Discovery Hub",
            category="💡Lightx02/Civitai",
            inputs=[
                comfy_io.String.Input(
                    "selection_data",
                    default="{}",
                    multiline=True,
                    tooltip="JSON payload set by the UI (selected item + flags).",
                )
            ],
            outputs=[
                comfy_io.String.Output(display_name="positive_prompt"),
                comfy_io.String.Output(display_name="negative_prompt"),
                comfy_io.Image.Output(display_name="image"),
                comfy_io.String.Output(display_name="info"),
                comfy_io.String.Output(display_name="workflow_json"),
            ],
        )

    @classmethod
    def is_changed(cls, selection_data: str, **kwargs):
        return selection_data

    @classmethod
    def execute(cls, selection_data: str):
        try:
            node_selection = json.loads(selection_data or "{}")
        except Exception:
            node_selection = {}

        item_data = node_selection.get("item", {}) if isinstance(node_selection, dict) else {}
        should_download = bool(node_selection.get("download_image", False)) if isinstance(node_selection, dict) else False

        meta = item_data.get("meta", {}) if isinstance(item_data, dict) else {}
        if not isinstance(meta, dict):
            meta = {}

        pos_prompt = meta.get("prompt") or meta.get("Prompt") or meta.get("positive") or meta.get("textPrompt") or ""
        neg_prompt = meta.get("negativePrompt") or meta.get("NegativePrompt") or meta.get("negative") or ""
        pos_prompt = str(pos_prompt or "")
        neg_prompt = str(neg_prompt or "")

        image_url = str(item_data.get("url") or "") if isinstance(item_data, dict) else ""

        # 处理工作流 JSON
        workflow_json = ""
        has_workflow = False
        if meta and isinstance(meta, dict):
            comfy_str = meta.get("comfy", "")
            if comfy_str:
                try:
                    # 解析 comfy 字符串为 JSON 对象
                    workflow_obj = json.loads(comfy_str)
                    # 转换回字符串，确保 UTF-8 编码正确处理中文
                    workflow_json = json.dumps(workflow_obj, indent=4, ensure_ascii=False)
                    has_workflow = True
                    
                    # 从工作流中提取提示词
                    workflow_prompt, workflow_neg_prompt = extract_prompts_from_workflow(workflow_json)
                    # 更新提示词（如果从工作流中提取到了）
                    if workflow_prompt:
                        pos_prompt = workflow_prompt
                    if workflow_neg_prompt:
                        neg_prompt = workflow_neg_prompt
                except json.JSONDecodeError:
                    pass

        info_dict = dict(meta)
        for k in ("prompt", "Prompt", "positive", "textPrompt", "negativePrompt", "NegativePrompt", "negative"):
            info_dict.pop(k, None)
        
        # 在 info 中添加工作流标记
        if has_workflow:
            info_dict["has_workflow"] = True

        try:
            info_string = json.dumps(info_dict, indent=4, ensure_ascii=False)
        except Exception:
            info_string = "{}"

        tensor = _empty_image_tensor()

        if should_download and image_url:
            try:
                tensor = _download_image_to_tensor(image_url, timeout_s=30)
            except Exception:
                tensor = _empty_image_tensor()

        return (pos_prompt, neg_prompt, tensor, info_string, workflow_json)


# ----- SECTION: Server Routes -----
prompt_server = PromptServer.instance


@prompt_server.routes.get("/civitai_gallery/get_all_favorites_data")
async def get_all_favorites_data(request):
    favorites = load_favorites()
    return web.json_response(favorites)


@prompt_server.routes.post("/civitai_gallery/toggle_favorite")
async def toggle_favorite(request):
    try:
        data = await request.json()
        item = data.get("item")
        if not isinstance(item, dict) or "id" not in item:
            return web.json_response({"status": "error", "message": "Invalid item data"}, status=400)

        item_id = str(item["id"])
        favorites = load_favorites()

        if item_id in favorites:
            del favorites[item_id]
            status = "removed"
        else:
            if item.get("meta") is None or not isinstance(item.get("meta"), dict):
                item["meta"] = {}
            if "tags" not in item or not isinstance(item.get("tags"), list):
                item["tags"] = []
            item["meta"].pop("prompt_saved", None)
            favorites[item_id] = item
            status = "added"

        save_favorites(favorites)
        return web.json_response({"status": status})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@prompt_server.routes.post("/civitai_gallery/add_or_update_favorite")
async def add_or_update_favorite(request):
    try:
        data = await request.json()
        item = data.get("item")
        if not isinstance(item, dict) or "id" not in item:
            return web.json_response({"status": "error", "message": "Invalid item data"}, status=400)

        item_id = str(item["id"])
        favorites = load_favorites()
        favorites[item_id] = item
        save_favorites(favorites)
        return web.json_response({"status": "success"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@prompt_server.routes.get("/civitai_gallery/get_favorites_images")
async def get_favorites_images(request):
    try:
        page = clamp_int(request.query.get("page", "1"), 1, 1_000_000, 1)
        limit = clamp_int(request.query.get("limit", "50"), 1, 200, 50)

        favorites = load_favorites()
        items = list(favorites.values())

        total_items = len(items)
        start_index = (page - 1) * limit
        end_index = start_index + limit
        paginated_items = items[start_index:end_index]

        return web.json_response(
            {
                "items": paginated_items,
                "metadata": {
                    "totalItems": total_items,
                    "currentPage": page,
                    "pageSize": limit,
                    "totalPages": (total_items + limit - 1) // limit,
                },
            }
        )
    except Exception as e:
        print(f"CivitaiDiscoveryHub: get_favorites_images error: {e}")
        return web.json_response({"error": str(e)}, status=500)


@prompt_server.routes.post("/civitai_gallery/update_favorite_tags")
async def update_favorite_tags(request):
    try:
        data = await request.json()
        item_id = str(data.get("id") or "")
        tags = data.get("tags", [])
        if not item_id:
            return web.json_response({"status": "error", "message": "Missing item id"}, status=400)
        if not isinstance(tags, list):
            tags = []

        favorites = load_favorites()
        if item_id in favorites:
            favorites[item_id]["tags"] = tags
            save_favorites(favorites)
            return web.json_response({"status": "success"})
        return web.json_response({"status": "error", "message": "Item not in favorites"}, status=404)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@prompt_server.routes.get("/civitai_gallery/get_all_favorite_tags")
async def get_all_favorite_tags(request):
    try:
        favorites = load_favorites()
        all_tags = set()
        for item in favorites.values():
            tags = item.get("tags")
            if isinstance(tags, list):
                for t in tags:
                    if isinstance(t, str) and t.strip():
                        all_tags.add(t.strip())
        sorted_tags = sorted(list(all_tags), key=lambda s: s.lower())
        return web.json_response({"tags": sorted_tags})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@prompt_server.routes.get("/civitai_gallery/images_stream")
async def get_civitai_images_stream(request):
    try:
        nsfw = (request.query.get("nsfw", "None") or "").strip()
        sort = (request.query.get("sort", "Most Reactions") or "").strip()
        period = (request.query.get("period", "Day") or "").strip()
        username = (request.query.get("username", "") or "").strip()
        tags_q = (request.query.get("tags", "") or "").strip()
        query_q = (request.query.get("query", "") or "").strip()
        types = (request.query.get("types", "") or "").strip()
        model_types = (request.query.get("modelTypes", "") or "").strip()
        tag_mode = (request.query.get("tagMode", "") or "").strip()
        model_id = (request.query.get("modelId", "") or "").strip() or None
        model_ver_id = (request.query.get("modelVersionId", "") or "").strip() or None
        post_id = (request.query.get("postId", "") or "").strip() or None

        include_videos = truthy(request.query.get("include_videos", "false"))
        hide_no_prompt = truthy(request.query.get("hide_no_prompt", "false"))
        videos_only = truthy(request.query.get("videos_only", "false"))

        cursor = request.query.get("cursor", None)
        min_batch = clamp_int(request.query.get("min_batch", "50"), 1, 500, 50)

        time_budget_ms = clamp_int(request.query.get("time_budget_ms", "0"), 0, 15000, 0)
        deadline = (time.monotonic() + (time_budget_ms / 1000.0)) if time_budget_ms > 0 else None

        international_version = truthy(request.query.get("international_version", "true"))
        base_domain = "civitai.com" if international_version else "civitai.work"
        base_url = f"https://{base_domain}/api/v1/images"

        model_id = (request.query.get("modelId", "") or "").strip() or None
        model_ver_id = (request.query.get("modelVersionId", "") or "").strip() or None

        upstream_limit = 200 if videos_only else 100

        def build_params(cur: Optional[str]) -> Dict[str, str]:
            p: Dict[str, str] = {
                "limit": str(upstream_limit),
                "nsfw": nsfw,
                "sort": sort,
                "period": period,
            }
            if username:
                p["username"] = username
            if tags_q:
                p["tags"] = tags_q
            if query_q:
                p["query"] = query_q
            if model_id:
                p["modelId"] = model_id
            if model_ver_id:
                p["modelVersionId"] = model_ver_id
            if types:
                p["types"] = types
            if model_types:
                p["modelTypes"] = model_types
            if tag_mode:
                p["tagMode"] = tag_mode
            if post_id:
                p["postId"] = post_id
            if cur:
                p["cursor"] = cur
            return p

        async def fetch_once(session: aiohttp.ClientSession, cur: Optional[str]) -> Dict[str, Any]:
            params = build_params(cur)
            
            # Add headers with API key if available
            headers = {}
            api_key = get_civitai_api_key()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            
            async with session.get(base_url, params=params, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return {"items": [], "metadata": {"error": f"upstream {resp.status}", "detail": text[:400]}, "next": None}
                try:
                    data = json.loads(text)
                except Exception:
                    return {"items": [], "metadata": {"error": "bad json"}, "next": None}

                md = data.get("metadata", {}) if isinstance(data, dict) else {}
                nxt = md.get("nextCursor") or md.get("cursor") or md.get("next") or None
                items = data.get("items", []) if isinstance(data, dict) else []
                if not isinstance(items, list):
                    items = []
                return {"items": items, "metadata": md, "next": nxt}

        started = time.monotonic()
        kept: List[Dict[str, Any]] = []
        dropped = 0
        next_cursor = None

        # 使用统一 HTTP 客户端（自动处理代理）
        session = await http_client.get_session()

        cur = cursor
        for _ in range(50):
            res = await fetch_once(session, cur)
            rec_items = res.get("items", [])
            next_cursor = res.get("next", None)

            for it in rec_items:
                if not isinstance(it, dict):
                    dropped += 1
                    continue

                if videos_only:
                    if not item_is_video(it):
                        dropped += 1
                        continue
                else:
                    if not include_videos and item_is_video(it):
                        dropped += 1
                        continue

                if hide_no_prompt and not item_has_positive_prompt(it):
                    dropped += 1
                    continue
                if query_q and not item_matches_query_local(it, query_q):
                    dropped += 1
                    continue

                kept.append(it)

            if len(kept) >= min_batch:
                break

            if deadline is not None and time.monotonic() >= deadline and len(kept) > 0:
                break

            if not next_cursor:
                break

            cur = next_cursor

        served = kept[:min_batch] if min_batch > 0 else kept

        meta_out = {
            "aggregated": True,
            "nextCursor": next_cursor,
            "served": len(served),
            "droppedByFilters": dropped,
            "hasMore": bool(next_cursor),
            "nsfw": nsfw,
            "sort": sort,
            "period": period,
            "videosOnly": videos_only,
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "timeBudgetMs": time_budget_ms,
        }
        return web.json_response({"items": served, "metadata": meta_out})
    except Exception as e:
        return web.json_response({"error": f"Unhandled: {e}"}, status=500)


@prompt_server.routes.post("/civitai_gallery/check_video_workflow")
async def check_video_workflow(request):
    data = await request.json()
    video_url = data.get("url")
    if not video_url:
        return web.json_response({"has_workflow": False, "error": "URL is missing"}, status=400)
    try:
        headers = {"Range": "bytes=0-4194304"}
        api_key = get_civitai_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # 使用统一 HTTP 客户端（自动处理代理）
        session = await http_client.get_session()
        async with session.get(video_url, headers=headers) as response:
            if response.status >= 400 and response.status != 416:
                return web.json_response(
                    {"has_workflow": False, "error": f"Failed to fetch video chunk, status: {response.status}"}
                )
            chunk = await response.content.read()
            has_workflow = b'"workflow":' in chunk or b'"prompt":' in chunk
            return web.json_response({"has_workflow": has_workflow})
    except Exception as e:
        return web.json_response({"has_workflow": False, "error": str(e)}, status=500)


@prompt_server.routes.get("/civitai_gallery/get_video_for_workflow")
async def get_video_for_workflow(request):
    video_url = request.query.get("url")
    if not video_url:
        return web.Response(status=400, text="Missing video URL")
    try:
        headers = {}
        api_key = get_civitai_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # 使用统一 HTTP 客户端（自动处理代理）
        session = await http_client.get_session()
        async with session.get(video_url, headers=headers) as response:
            if response.status != 200:
                return web.Response(status=response.status, text=f"Failed to fetch video from source: {response.reason}")
            data = await response.read()
            filename = video_url.split("/")[-1].split("?")[0] or "video_with_workflow.mp4"
            return web.Response(
                body=data,
                content_type=response.content_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
    except Exception as e:
        return web.Response(status=500, text=str(e))


@prompt_server.routes.post("/civitai_gallery/reload_proxy")
async def reload_proxy_endpoint(request):
    """
    热切换代理配置端点

    调用此端点会强制重新加载 config.json 中的代理配置，
    并重建 HTTP 客户端 session，实现无需重启的热切换。

    请求体（可选）：
    {
        "config": {  // 临时覆盖配置，不会写入文件
            "enabled": true,
            "type": "http",
            "host": "127.0.0.1",
            "port": 10808
        }
    }
    """
    try:
        data = await request.json() if request.can_read_body else {}
        temp_config = data.get("config")

        if temp_config and isinstance(temp_config, dict):
            # 临时覆盖配置（仅内存，不写入文件）
            global _config_cache
            if _config_cache is None:
                _config_cache = {}
            if "proxy" not in _config_cache:
                _config_cache["proxy"] = {}
            _config_cache["proxy"].update(temp_config)
            print(f"CivitaiDiscoveryHub: Proxy config temporarily updated: {temp_config}")

        # 强制重新加载代理配置
        result = await http_client.reload_proxy()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@prompt_server.routes.get("/civitai_gallery/proxy_status")
async def get_proxy_status(request):
    """获取当前代理状态"""
    try:
        settings = get_proxy_settings()
        return web.json_response({
            "enabled": settings["enabled"],
            "type": settings["type"],
            "host": settings["host"],
            "port": settings["port"],
            "has_auth": bool(settings["username"] and settings["password"]),
            "current_proxy_url": http_client._current_proxy.split("@")[-1] if http_client._current_proxy else None,
        })
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


# ----- SECTION: Entry Point (comfy_entrypoint) -----
class CivitaiDiscoveryHubExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[comfy_io.ComfyNode]]:
        return [CivitaiDiscoveryHubNode]


async def comfy_entrypoint() -> ComfyExtension:
    return CivitaiDiscoveryHubExtension()
