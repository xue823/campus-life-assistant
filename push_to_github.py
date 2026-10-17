# -*- coding: utf-8 -*-
"""
把当前目录发布到 GitHub（用 GitHub REST API，不需要安装 git）。

用法（PowerShell，在 campus_life_upload 目录下）：
    $env:GITHUB_TOKEN="你的Personal Access Token"
    $env:PYTHONIOENCODING="utf-8"
    runtime/python/python.exe push_to_github.py --owner 你的用户名 --repo campus-life-assistant

令牌要求：Fine-grained token（勾选 Contents: Read and write + Administration: Read and write）
          或 Classic token（勾选 repo 权限）。

安全说明：令牌只在内存使用，不写入任何文件；用完请到 GitHub Settings → Developer settings
          → Personal access tokens 删除。
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
SKIP_DIRS = {"__pycache__", ".idea", "runtime", "data", ".git", "node_modules"}
SKIP_EXT = {".pyc", ".log", ".db", ".sqlite", ".zip"}

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def call(method, path, token, data=None, timeout=40):
    url = API + path
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + token,
        "User-Agent": "campus-life-uploader",
        "Content-Type": "application/json;charset=UTF-8",
    }
    if method == "GET" and data:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(data)
    body = json.dumps(data).encode("utf-8") if (method != "GET" and data is not None) else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw[:300]}
        return exc.code, parsed
    except Exception as exc:
        return 0, {"error": str(exc)}


def collect(root):
    files = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(base, name)
            files.append((os.path.relpath(full, root).replace("\\", "/"), full))
    files.sort()
    return files


def main():
    ap = argparse.ArgumentParser(description="发布到 GitHub")
    ap.add_argument("--owner", default="")
    ap.add_argument("--repo", default="campus-life-assistant")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("[错误] 没有设置 GITHUB_TOKEN 环境变量。")
        print('       PowerShell 里先执行： $env:GITHUB_TOKEN="你的令牌"')
        sys.exit(1)

    status, me = call("GET", "/user", token)
    if status == 401:
        print("[错误] 令牌无效或已过期（HTTP 401）。")
        sys.exit(2)
    if status != 200:
        print("[错误] 调用 /user 失败：HTTP %s %s" % (status, me))
        sys.exit(2)
    login = me.get("login", "")
    owner = args.owner or login
    print("令牌有效，登录名：%s" % login)
    if login.lower() != owner.lower():
        print("注意：上传到 %s，而令牌属于 %s" % (owner, login))

    status, repo = call("GET", "/repos/%s/%s" % (owner, args.repo), token)
    if status == 200:
        branch = repo.get("default_branch") or args.branch
        print("仓库已存在，复用：%s（默认分支 %s）" % (repo.get("html_url"), branch))
    else:
        status, repo = call("POST", "/user/repos", token, {
            "name": args.repo,
            "description": ("黄淮校园生活助手 (Campus Life Copilot) - 考核安排 / 干饭推荐 / "
                            "记账分析 / 同学名单 / 官方入口 / 校园问答"),
            "private": False,
            "auto_init": True,
            "has_issues": True,
        })
        if status not in (200, 201):
            print("[错误] 创建仓库失败：HTTP %s" % status)
            print(json.dumps(repo, ensure_ascii=False, indent=2)[:800])
            sys.exit(3)
        branch = repo.get("default_branch") or args.branch
        print("仓库创建成功：%s（默认分支 %s）" % (repo.get("html_url"), branch))
        time.sleep(2)

    root = os.path.abspath(args.dir)
    files = collect(root)
    print("待上传 %d 个文件" % len(files))

    ok = 0
    for rel, full in files:
        with open(full, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        path = "/repos/%s/%s/contents/%s" % (owner, args.repo,
                                             urllib.parse.quote(rel))
        st, info = call("GET", path + "?ref=" + urllib.parse.quote(branch), token)
        payload = {"message": "feat: 上传 %s" % rel, "content": content,
                   "branch": branch}
        method = "PUT"
        if st == 200 and isinstance(info, dict) and info.get("sha"):
            payload["sha"] = info["sha"]
        st, res = call(method, path, token, payload)
        if st in (200, 201):
            ok += 1
            print("  成功 %-42s %s" % (rel, "更新" if "sha" in payload else "新增"))
        else:
            print("  失败 %-42s HTTP %s %s" % (rel, st, str(res)[:200]))
        time.sleep(0.15)

    html = "https://github.com/%s/%s" % (owner, args.repo)
    print("\n完成：%d/%d 个文件" % (ok, len(files)))
    print("仓库地址       ：%s" % html)
    print("封面图(jsDelivr)：https://cdn.jsdelivr.net/gh/%s/%s@%s/cover.png"
          % (owner, args.repo, branch))
    print("封面图(raw)     ：https://github.com/%s/%s/raw/%s/cover.png"
          % (owner, args.repo, branch))
    print("\n匿名验证：")
    for url in (html,
                "https://api.github.com/repos/%s/%s" % (owner, args.repo),
                "https://cdn.jsdelivr.net/gh/%s/%s@%s/cover.png"
                % (owner, args.repo, branch)):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                print("  HTTP %s  %s" % (resp.status, url))
        except urllib.error.HTTPError as exc:
            print("  HTTP %s  %s" % (exc.code, url))
        except Exception as exc:
            print("  失败     %s（%s）" % (url, str(exc)[:60]))


if __name__ == "__main__":
    main()
