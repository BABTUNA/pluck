"""
three ways to get the pages own data ordered cheapest first
  declared  parse the json ld the merchant wrote for google
  shipped   parse the json state frameworks embed as inert script tags
  computed  run the pages inline js in a v8 sandbox and read the state it builds
"""

import json
import re
import threading

_VM_LOCK = threading.Lock()
_SCRIPT = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script>", re.I)
_TYPE = re.compile(r"type\s*=\s*[\"']([^\"']+)", re.I)


# grab every inline script body with its type attribute
def scripts(html: str) -> list[tuple[str, str]]:
    out = []
    for m in _SCRIPT.finditer(html):
        attrs, body = m.group(1) or "", m.group(2)
        if re.search(r"\bsrc\s*=", attrs, re.I) or not body.strip():
            continue
        out.append((((m := _TYPE.search(attrs)) and m.group(1).lower()) or "", body))
    return out


# forgiving json parse that tolerates trailing garbage after the document
def _loads(body: str):
    try:
        return json.JSONDecoder().raw_decode(body.strip())[0]
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------- rung a --
# parse the json ld blocks
def declared(scr: list[tuple[str, str]]) -> list:
    return [o for t, body in scr if t == "application/ld+json"
            and (o := _loads(body)) is not None]


# ---------------------------------------------------------------- rung b --
# parse inert json state tags plus window assignments of json literals
_ASSIGN = re.compile(r"window\.__[A-Z_]+__\s*=\s*")


def shipped(scr: list[tuple[str, str]]) -> list:
    out = []
    for t, body in scr:
        if "json" in t and t != "application/ld+json" and len(body) > 200 \
                and (o := _loads(body)) is not None:
            out.append(o)
        elif t in ("", "text/javascript") and len(body) > 200:
            for m in _ASSIGN.finditer(body[:600_000]):
                try:
                    out.append(json.JSONDecoder().raw_decode(body, m.end())[0])
                except (json.JSONDecodeError, ValueError):
                    pass
    return out


# ---------------------------------------------------------------- rung c --
_PRELUDE = """
var window = globalThis; var self = globalThis; var global = globalThis;
var noop = function(){}; var el = function(){ return {textContent:"", innerHTML:"", style:{},
  setAttribute:noop, appendChild:noop, classList:{add:noop, remove:noop}}; };
var document = { getElementById: el, createElement: el, querySelector: function(){ return null; },
  querySelectorAll: function(){ return []; }, addEventListener: noop, currentScript: {textContent:""},
  documentElement: el(), head: el(), body: el(), cookie: "", readyState: "loading", title: "" };
var location = {href:"https://x.com/", hostname:"x.com", pathname:"/", search:"", protocol:"https:", origin:"https://x.com"};
var navigator = {userAgent:"Mozilla/5.0", language:"en-US", languages:["en-US"]};
var localStorage = {getItem:function(){return null;}, setItem:noop, removeItem:noop};
var sessionStorage = localStorage; var addEventListener = noop; var removeEventListener = noop;
var setTimeout = function(){return 0;}; var setInterval = setTimeout; var clearTimeout = noop; var clearInterval = noop;
var fetch = function(){ throw new Error("no network"); }; var XMLHttpRequest = fetch;
var __baseline = {}; (function(){ for (var k in globalThis) __baseline[k] = true; })();
"""

_SNAPSHOT = """
(function(){
  var out = {}; var seen = [];
  function safe(v, d) {
    if (d > 7 || v === null || v === undefined) return v === undefined ? null : v;
    var t = typeof v;
    if (t === "number" || t === "string" || t === "boolean") return v;
    if (t !== "object" || seen.indexOf(v) !== -1) return undefined;
    seen.push(v);
    if (Array.isArray(v)) { var a = []; for (var i = 0; i < v.length && i < 200; i++) a.push(safe(v[i], d+1)); return a; }
    var o = {}; var n = 0;
    for (var k in v) { if (n++ > 200) break; var s = safe(v[k], d+1); if (s !== undefined) o[k] = s; }
    return o;
  }
  for (var k in globalThis) {
    if (__baseline[k] || k === "__baseline") continue;
    try { var v = globalThis[k];
      if (typeof v === "object" && v !== null) {
        var j = JSON.stringify(safe(v, 0));
        if (j && j.length > 300) out[k] = j.slice(0, 1500000); } } catch (e) {}
  }
  return JSON.stringify(out);
})()
"""

_STATEY = re.compile(
    r"window\.|self\.|globalThis|__NEXT|__NUXT|__INITIAL|__APOLLO|__PRELOADED"
    r"|JSON\.parse|__remix|Shopify|_state_|__STATE", re.I)


# boot a fake browser and run the pages state building scripts, then
# snapshot whatever new globals they created; one page at a time since
# v8 isolates dont like concurrent teardown
def computed(scr: list[tuple[str, str]]) -> list:
    with _VM_LOCK:
        return _computed(scr)


def _computed(scr: list[tuple[str, str]]) -> list:
    from py_mini_racer import MiniRacer
    ctx = MiniRacer()
    ctx.eval(_PRELUDE)
    ran = 0
    for t, body in scr:
        if t not in ("", "text/javascript", "module") or not (300 < len(body) < 2_000_000):
            continue
        if not _STATEY.search(body):
            continue
        # a pages broken script is its own problem so run the rest
        try:
            ctx.eval(body, timeout_sec=1.5, max_memory=256 * 1024 * 1024)
            ran += 1
        except Exception:  # noqa: BLE001
            pass
        if ran >= 60:
            break
    try:
        snap = json.loads(ctx.eval(_SNAPSHOT, timeout_sec=3.0))
    except Exception:  # noqa: BLE001
        return []
    return [o for j in snap.values() if (o := _loads(j)) is not None]
