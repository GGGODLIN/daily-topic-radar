#!/usr/bin/env bash
# cc-tool-updates-daily.sh — local-analysis shell channel
# 枚舉 5 manager + 三名單分類 + 比對 upstream → 預設 markdown report（無更新+無新發現 → __SILENT__）。
# --json：結構化輸出給測試。graceful：任何查詢失敗進 errors、不中斷、exit 0。
# 名單同目錄：cc-tool-manifest.json（白）/ cc-tool-ignore.txt（黑）。
# 重要性判斷（該升/可緩）交給 workflow digest LLM 階段，本 script 只附 release notes 摘要當素材。
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CCTOOL_DIR="$DIR"
case " $* " in
  *" --json "*) : ;;
  *)
    SI="/Users/linhancheng/code/social-info"
    export CCTOOL_OUT="${CCTOOL_OUT:-$SI/reports/local-analysis/$(date +%F)-tool-updates.md}"
    mkdir -p "$(dirname "$CCTOOL_OUT")" 2>/dev/null || true
  ;;
esac
python3 - "$@" <<'PY'
import sys, os, json, re, subprocess, urllib.request

DIR = os.environ["CCTOOL_DIR"]
JSON_OUT = "--json" in sys.argv[1:]
MANIFEST = os.environ.get("CCTOOL_MANIFEST") or os.path.join(DIR, "cc-tool-manifest.json")
IGNORE = os.environ.get("CCTOOL_IGNORE") or os.path.join(DIR, "cc-tool-ignore.txt")

def run(args, timeout=20):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None

def run_raw(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return None

def load_manifest():
    try:
        return json.load(open(MANIFEST))
    except Exception:
        return []

def load_ignore():
    try:
        return set(l.strip() for l in open(IGNORE) if l.strip() and not l.startswith("#"))
    except Exception:
        return set()

def norm(v):
    return re.sub(r'^v', '', (v or "").strip())

# ---- installed 偵測 ----
def cargo_git_installed():
    out = {}
    try:
        d = json.load(open(os.path.expanduser("~/.cargo/.crates2.json")))
    except Exception:
        return out
    for k, v in d.get("installs", {}).items():
        m = re.match(r'(\S+)\s+(\S+)\s+\(git\+(\S+?)(?:\?(tag|branch|rev)=([^#]+))?#([0-9a-f]+)\)', k)
        if not m:
            continue
        pkg, _ver, url, _rt, ref, commit = m.groups()
        repo = re.sub(r'https?://github.com/|\.git$', '', url)
        for b in (v.get("bins") or [pkg]):
            out[b] = {"repo": repo, "tag": ref, "commit": commit}
    return out

def brew_installed():
    out = {}
    s = run(["brew", "list", "--versions"])
    if s:
        for line in s.splitlines():
            p = line.split()
            if len(p) >= 2:
                out[p[0]] = p[-1]
    return out

def brew_leaves():
    s = run(["brew", "leaves"])
    return [l.strip() for l in s.splitlines() if l.strip()] if s else []

def npm_g_installed():
    out = {}
    s = run_raw(["npm", "ls", "-g", "--depth=0", "--json"])
    if s:
        try:
            for name, info in json.loads(s).get("dependencies", {}).items():
                out[name] = info.get("version")
        except Exception:
            pass
    return out

def uv_installed():
    out = {}
    s = run(["uv", "tool", "list"])
    if s:
        for line in s.splitlines():
            m = re.match(r'(\S+)\s+v?([\d.]+)', line)
            if m:
                out[m.group(1)] = m.group(2)
    return out

def mcp_list():
    s = run(["claude", "mcp", "list"])
    names = []
    if s:
        for line in s.splitlines():
            m = re.match(r'([A-Za-z0-9_-]+):', line.strip())
            if m:
                names.append(m.group(1))
    return names

# ---- upstream latest ----
def gh_latest(repo):
    s = run(["gh", "api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"])
    if s and s.strip():
        return s.strip()
    s = run(["gh", "api", f"repos/{repo}/tags", "--jq", ".[0].name"])
    return s.strip() if s and s.strip() else None

def gh_notes(repo, tag):
    s = run(["gh", "api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".body"])
    return " ".join(s.split())[:200] if s else ""

def npm_latest(pkg):
    s = run(["npm", "view", pkg, "version"])
    return s.strip() if s else None

def pypi_latest(pkg):
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=15) as r:
            return json.load(r)["info"]["version"]
    except Exception:
        return None

def brew_latest(formula):
    s = run(["brew", "info", "--json=v2", formula])
    if not s:
        return None
    try:
        return (json.loads(s).get("formulae") or [{}])[0].get("versions", {}).get("stable")
    except Exception:
        return None

manifest = load_manifest()
ignore = load_ignore()
cargo, brew, npm, uv = cargo_git_installed(), brew_installed(), npm_g_installed(), uv_installed()
updates, errors = [], []

def add_update(name, mgr, cur, latest, src, notes=""):
    if latest and norm(latest) != norm(cur):
        updates.append({"name": name, "manager": mgr, "current": cur, "latest": latest, "source": src, "notes": notes})

for e in manifest:
    name, mgr, src = e.get("name"), e.get("manager"), e.get("source")
    try:
        if mgr == "cargo-git":
            info = cargo.get(name)
            if not info:
                errors.append({"name": name, "reason": "not in .crates2.json"}); continue
            cur = info["tag"] or info["commit"][:8]
            latest = gh_latest(info["repo"])
            add_update(name, mgr, cur, latest, src or info["repo"], gh_notes(info["repo"], latest) if latest else "")
        elif mgr == "github-release":
            v = run([name, "--version"]) or ""
            mm = re.search(r'(\d+\.\d+\.\d+)', v)
            if not mm:
                errors.append({"name": name, "reason": "cannot detect installed version"}); continue
            latest = gh_latest(src)
            add_update(name, mgr, mm.group(1), latest, src, gh_notes(src, latest) if latest else "")
        elif mgr == "brew":
            cur = brew.get(src or name)
            if not cur:
                errors.append({"name": name, "reason": "not brew-installed"}); continue
            add_update(name, mgr, cur, brew_latest(src or name), src or name)
        elif mgr == "npm-g":
            cur = npm.get(src or name)
            if not cur:
                errors.append({"name": name, "reason": "not npm-g-installed"}); continue
            add_update(name, mgr, cur, npm_latest(src or name), src or name)
        elif mgr == "uv-tool":
            cur = uv.get(src or name)
            if not cur:
                errors.append({"name": name, "reason": "not uv-installed"}); continue
            add_update(name, mgr, cur, pypi_latest(src or name), src or name)
        else:
            errors.append({"name": name, "reason": f"unknown manager {mgr}"})
    except Exception as ex:
        errors.append({"name": name, "reason": str(ex)[:80]})

tracked = set(e.get("name") for e in manifest)
discovered = []
for names, mgr in [(cargo.keys(), "cargo-git"), (brew_leaves(), "brew"),
                   (npm.keys(), "npm-g"), (uv.keys(), "uv-tool"), (mcp_list(), "mcp")]:
    for n in names:
        if n not in tracked and n not in ignore:
            discovered.append({"name": n, "manager": mgr})

result = {"updates": updates, "discovered": discovered, "errors": errors}

if JSON_OUT:
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)

def emit(text):
    op = os.environ.get("CCTOOL_OUT")
    if op:
        try:
            open(op, "w").write(text + "\n")
        except Exception:
            pass
    print(text)

if not updates and not discovered:
    emit("__SILENT__")
    sys.exit(0)

out = []
if updates:
    out.append("### 有更新")
    for u in updates:
        note = f" — release notes: {u['notes']}" if u.get("notes") else ""
        out.append(f"- {u['name']} {u['current']}→{u['latest']}（{u['manager']}）{note}")
if discovered:
    out.append("### 待分類（新發現，不在白/黑名單；歸白名單追蹤 or 黑名單忽略）")
    for d in discovered:
        out.append(f"- {d['name']}（{d['manager']}）")
if errors:
    out.append("### 查詢失敗（graceful skip）")
    for e in errors:
        out.append(f"- {e['name']}: {e['reason']}")
emit("\n".join(out))
PY
