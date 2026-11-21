#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
real_usage.py  —  2.2-auto  双保险版本
    (1) 接管 requests.post：能拿到接口 usage 就记真实值
    (2) 包裹 call_* 函数：不管底层用什么库（httpx / openai SDK / aiohttp），
        只要模型调用经过 call_xxx，就一定能记到一条

接入（app.py 顶部，import 之后、import trading_system 之前）：
    import real_usage
    real_usage.install(debug=True)

按 Run 的分支里：      real_usage.reset()
流水线结束后：          t = real_usage.current().totals()
面板显示：              real_usage.current().label()

自检：
    python -c "import real_usage; print(real_usage.VERSION, real_usage.status())"
"""

from __future__ import annotations

import functools
import json
import re
import sys
import time
from pathlib import Path

VERSION = "2.2-auto"

PRICES: dict[str, tuple[float, float]] = {
    "doubao-seed-1.6":       (0.80, 2.40),
    "doubao-seed-1.6-flash": (0.30, 0.60),
    "doubao-seed-2.1-pro":   (6.00, 30.00),
    "doubao-pro":            (0.80, 2.00),
    "doubao-lite":           (0.30, 0.60),
}
DEFAULT_PRICE = (0.80, 2.40)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_METER = None
_INSTALLED = False
_DEBUG = False
_WRAPPED: list[str] = []


# ------------------------------------------------------------------ 工具
def price_of(model: str) -> tuple[float, float]:
    key = (model or "").lower()
    if key in PRICES:
        return PRICES[key]
    for name, price in PRICES.items():
        if name in key:
            return price
    return DEFAULT_PRICE


def ensure_stream_usage(payload: dict) -> dict:
    payload.setdefault("stream_options", {"include_usage": True})
    return payload


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    other = len(CJK_RE.sub("", text))
    return max(1, int(cjk + other / 3.5))


NODE_HINTS = [("technical", "technical"), ("技术", "technical"),
              ("fundamental", "fundamental"), ("基本面", "fundamental"),
              ("sentiment", "sentiment"), ("情绪", "sentiment"),
              ("bull", "debate"), ("bear", "debate"), ("debate", "debate"),
              ("decision", "decision"), ("trader", "decision"), ("synthes", "decision")]


def guess_node(payload: dict) -> str:
    try:
        text = " ".join(str(m.get("content", "")) for m in payload.get("messages", [])).lower()
    except Exception:
        return "agent"
    for key, label in NODE_HINTS:
        if key in text:
            return label
    return "agent"


def _prompt_text(payload) -> str:
    try:
        return "\n".join(str(m.get("content", "")) for m in payload.get("messages", []))
    except Exception:
        return str(payload)[:2000] if payload else ""


# ------------------------------------------------------------------ 计量器
class UsageMeter:
    def __init__(self, log_path: str | Path | None = None):
        self.records: list[dict] = []
        self.log_path = Path(log_path) if log_path else None

    def add_from_response(self, payload, node: str = "", model: str = "") -> dict | None:
        usage = payload.get("usage") if isinstance(payload, dict) else None

# 🌟 严谨处理：如果流式传输没有返回 usage 字典，仅在能拿到真实大模型文本时，利用文本长度进行真实估算
        if not isinstance(usage, dict) or not usage:
            prompt_text = payload.get("prompt_text", "") if isinstance(payload, dict) else ""
            completion_text = payload.get("completion_text", "") if isinstance(payload, dict) else ""
            if prompt_text or completion_text:
                return self.add_estimate(prompt_text, completion_text, node=node, model=model)
            # 📌 尊重你的原则：如果真的什么真实数据/文本都拿不到，严谨返回 None（如实呈现拿不到的结果）
            return None
        
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        if prompt == 0 and completion == 0:
            return None
        rec = {"node": node, "model": model, "prompt": prompt, "completion": completion,
               "total": int(usage.get("total_tokens") or prompt + completion),
               "source": "api", "ts": time.time()}
        self.records.append(rec)
        self._persist(rec)
        return rec

    def add_estimate(self, prompt_text: str, completion_text: str, node: str = "", model: str = "") -> dict:
        prompt, completion = estimate_tokens(prompt_text), estimate_tokens(completion_text)
        rec = {"node": node, "model": model, "prompt": prompt, "completion": completion,
               "total": prompt + completion, "source": "estimate", "ts": time.time()}
        self.records.append(rec)
        self._persist(rec)
        return rec

    def _persist(self, rec: dict) -> None:
        if not self.log_path:
            return
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def totals(self) -> dict:
        prompt = sum(r["prompt"] for r in self.records)
        completion = sum(r["completion"] for r in self.records)
        cost = sum(r["prompt"] / 1_000_000 * price_of(r["model"])[0]
                   + r["completion"] / 1_000_000 * price_of(r["model"])[1] for r in self.records)
        real = any(r["source"] == "api" for r in self.records)
        return {"calls": len(self.records), "prompt": prompt, "completion": completion,
                "total": prompt + completion, "cost_cny": round(cost, 6),
                "cost_usd": round(cost / 7.2, 6), "verified": real,
                "mixed": real and any(r["source"] == "estimate" for r in self.records)}

    def by_node(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for r in self.records:
            row = agg.setdefault(r["node"], {"node": r["node"], "calls": 0, "prompt": 0,
                                             "completion": 0, "total": 0, "source": r["source"]})
            row["calls"] += 1
            row["prompt"] += r["prompt"]
            row["completion"] += r["completion"]
            row["total"] += r["total"]
            if r["source"] == "estimate":
                row["source"] = "estimate"
        return sorted(agg.values(), key=lambda x: -x["total"])

    def label(self) -> str:
        t = self.totals()
        if not t["calls"]:
            return "No LLM call captured."
        if not t["verified"]:
            return "Estimated from text length; the API returned no usage block."
        if t["mixed"]:
            return "Partly metered, partly estimated."
        return "Metered from the API usage block."

    def summary_line(self) -> str:
        t = self.totals()
        return (f"{t['total']:,} tokens (in {t['prompt']:,} / out {t['completion']:,}) over "
                f"{t['calls']} calls · CNY {t['cost_cny']:.4f} · USD {t['cost_usd']:.6f}")

    def reset(self) -> None:
        self.records.clear()


# ------------------------------------------------------------------ 单例
def current() -> UsageMeter:
    global _METER
    if _METER is None:
        _METER = UsageMeter()
    return _METER


def reset() -> None:
    current().reset()


def status() -> str:
    return (f"{VERSION} installed={_INSTALLED} records={len(current().records)} "
            f"wrapped={_WRAPPED}")


# ------------------------------------------------------------------ 钩子一：HTTP
def _patch_requests(meter: UsageMeter) -> bool:
    try:
        import requests
    except ImportError:
        return False

    original_post = requests.post

    def patched_post(url, *args, **kwargs):
        payload = kwargs.get("json")
        streaming = bool(kwargs.get("stream")) or bool(isinstance(payload, dict) and payload.get("stream"))
        if isinstance(payload, dict) and payload.get("stream"):
            ensure_stream_usage(payload)
        if _DEBUG:
            print(f"[real_usage] POST {str(url)[:70]} stream={streaming}", flush=True)

        resp = original_post(url, *args, **kwargs)
        if streaming:
            if _DEBUG:
                print("[real_usage]   stream=True → 不在 HTTP 层读（交给函数层兜底）", flush=True)
            return resp

        model = payload.get("model", "") if isinstance(payload, dict) else ""
        node = guess_node(payload) if isinstance(payload, dict) else "agent"
        try:
            data = resp.json()
        except Exception:
            return resp

        if meter.add_from_response(data, node=node, model=model):
            if _DEBUG:
                print(f"[real_usage]   usage 命中 {meter.records[-1]['total']} tokens", flush=True)
            return resp

        text = ""
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except Exception:
            pass
        meter.add_estimate(_prompt_text(payload) if isinstance(payload, dict) else "",
                           text, node=node, model=model)
        if _DEBUG:
            keys = list(data.keys()) if isinstance(data, dict) else type(data)
            print(f"[real_usage]   usage 缺失 → 估算 {meter.records[-1]['total']} tokens keys={keys}", flush=True)
        return resp

    requests.post = patched_post
    try:
        requests.Session.post = lambda self, url, *a, **kw: patched_post(url, *a, **kw)
    except Exception:
        pass
    return True


# ------------------------------------------------------------------ 钩子二：函数包裹
def _should_wrap(name: str) -> bool:
    low = name.lower()
    if low.startswith("_"):
        return False
    return low.startswith(("call_", "ask_", "chat_", "llm_", "query_llm", "invoke_llm"))


def _wrap_fn(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        before = len(current().records)
        result = fn(*args, **kwargs)
        if len(current().records) == before:          # 没有 API 级记录 → 函数级兜底
            prompt = ""
            for cand in list(args) + list(kwargs.values()):
                if isinstance(cand, str) and len(cand) > 20:
                    prompt = cand
                    break
            out = result if isinstance(result, str) else (str(result) if result is not None else "")
            node = fn.__name__.replace("call_", "").replace("_", " ").strip() or "agent"
            current().add_estimate(prompt, out, node=node)
            if _DEBUG:
                print(f"[real_usage]   {fn.__name__} → 函数级估算 {current().records[-1]['total']} tokens",
                      flush=True)
        return result

    wrapper.__wrapped_original__ = fn
    return wrapper


def _patch_call_functions(meter: UsageMeter) -> bool:
    """在 sys.modules 里找 call_* 之类的函数，并同步替换所有已绑定引用。"""
    targets: list[object] = []
    seen: dict[int, object] = {}
    for mod in list(sys.modules.values()):
        if mod is None or mod.__name__.startswith("real_usage"):
            continue
        for attr in dir(mod):
            if not _should_wrap(attr):
                continue
            try:
                obj = getattr(mod, attr)
            except Exception:
                continue
            if callable(obj) and not hasattr(obj, "__wrapped_original__") and id(obj) not in seen:
                seen[id(obj)] = obj
                targets.append(obj)

    # 再扫一遍类里的同名方法（call_doubao 若是某个类的方法，也要包住）
    class_slots: list[tuple[type, str, object]] = []
    for mod in list(sys.modules.values()):
        if mod is None or mod.__name__.startswith("real_usage"):
            continue
        for cls_name in dir(mod):
            try:
                cls = getattr(mod, cls_name)
            except Exception:
                continue
            if not isinstance(cls, type):
                continue
            for attr in dir(cls):
                if not _should_wrap(attr):
                    continue
                try:
                    obj = getattr(cls, attr)
                except Exception:
                    continue
                if callable(obj) and not hasattr(obj, "__wrapped_original__"):
                    class_slots.append((cls, attr, obj))
                    if id(obj) not in seen:
                        seen[id(obj)] = obj
                        targets.append(obj)

    if not targets:
        if _DEBUG:
            print("[real_usage] 未找到 call_* 函数可包裹", flush=True)
        return False

    # 先全部包好，再统一替换，避免边遍历边改
    wrapped: dict[int, object] = {}
    for obj in targets:
        wrapped[id(obj)] = _wrap_fn(obj)
        _WRAPPED.append(getattr(obj, "__name__", "?"))

    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        for attr in dir(mod):
            try:
                val = getattr(mod, attr)
            except Exception:
                continue
            key = id(val)
            if key in wrapped:
                try:
                    setattr(mod, attr, wrapped[key])
                except Exception:
                    continue

    # 类方法必须写回类本身，否则 self.call_doubao 仍指向原函数
    for cls, attr, obj in class_slots:
        key = id(obj)
        if key in wrapped:
            try:
                setattr(cls, attr, wrapped[key])
            except Exception:
                continue
    return bool(wrapped)


def install(log_path: str | Path | None = None, debug: bool = False) -> UsageMeter:
    global _INSTALLED, _DEBUG
    meter = current()
    _DEBUG = debug
    if log_path:
        meter.log_path = Path(log_path)
    if _INSTALLED:
        return meter

    http_ok = _patch_requests(meter)
    fn_ok = _patch_call_functions(meter)
    _INSTALLED = True
    if debug:
        print(f"[real_usage] {VERSION} 就绪 · HTTP钩子={http_ok} · 函数包裹={_WRAPPED or '无'}", flush=True)
    return meter


if __name__ == "__main__":
    print("VERSION:", VERSION)
    m = UsageMeter()
    m.add_from_response({"usage": {"prompt_tokens": 812, "completion_tokens": 96}}, "technical", "doubao-seed-1.6")
    m.add_estimate("x" * 300, "y" * 80, "decision", "doubao-seed-1.6")
    print(m.summary_line())
    print(m.label())
    print(status())
