# -*- coding: utf-8 -*-
"""
一键把当前目录发布到 Gitee（用 Gitee OpenAPI，不需要安装 git）。

用法（PowerShell，在 campus_life_upload 目录下）：
    $env:GITEE_TOKEN="你的私人令牌"
    runtime/python/python.exe push_to_gitee.py

可选参数：
    --owner 17993              仓库所属用户名（默认取令牌对应的登录名）
    --repo campus-life-assistant
    --private false            公开仓库（别人打开链接能看到）
    --dir .                    要上传的目录
    --branch master            默认分支

安全说明：
    · 令牌只在内存中使用，不写入任何文件；
    · 用完请到 Gitee「设置 → 私人令牌」删除或重置；
    · 本脚本只做“创建仓库 + 上传文件”，不删除、不改动账号其他内容。
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

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

API = "https://gitee.com/api/v5"
SKIP_DIRS = {"__pycache__", ".idea", "runtime", "data", ".git", "node_modules"}
SKIP_EXT = {".pyc", ".log", ".db", ".sqlite", ".zip"}


def call(method, path, token, data=None, timeout=30):
    url = API + path
    headers = {"Content-Type": "application/json;charset=UTF-8"}
    if method == "GET":
        params = dict(data or {})
        params["access_token"] = token
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        payload = dict(data or {})
        payload["access_token"] = token
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method=method)
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
            rel = os.path.relpath(full, root).replace("\\", "/")
            files.append((rel, full))
    files.sort()
    return files


def main():
    ap = argparse.ArgumentParser(description="发布到 Gitee")
    ap.add_argument("--owner", default="")
    ap.add_argument("--repo", default="campus-life-assistant")
    ap.add_argument("--private", default="false")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--branch", default="master")
    args = ap.parse_args()

    token = os.environ.get("GITEE_TOKEN", "").strip()
    if not token:
        print("[错误] 没有设置 GITEE_TOKEN 环境变量。")
        print('       PowerShell 里先执行： $env:GITEE_TOKEN="你的私人令牌"')
        sys.exit(1)

    status, me = call("GET", "/user", token)
    if status == 401:
        print("[错误] 令牌无效或已过期（HTTP 401）。请到 Gitee → 设置 → 私人令牌 重新生成。")
        sys.exit(2)
    if status != 200:
        print("[错误] 调用 /user 失败：HTTP %s %s" % (status, me))
        sys.exit(2)
    login = me.get("login") or me.get("name") or ""
    owner = args.owner or login
    print("令牌有效，登录名：%s" % login)

    status, repo = call("GET", "/repos/%s/%s" % (urllib.parse.quote(owner),
                                                 urllib.parse.quote(args.repo)), token)
    if status == 200:
        print("仓库已存在，复用：%s" % repo.get("html_url"))
    else:
        status, repo = call("POST", "/user/repos", token, {
            "name": args.repo,
            "private": str(args.private).lower() == "true",
            "auto_init": True,
            "has_issues": True,
            "has_wiki": True,
            "description": ("黄淮校园生活助手 (Campus Life Copilot) - 考核安排 / 干饭推荐 / "
                            "记账分析 / 同学名单 / 官方入口 / 校园问答"),
        })
        if status not in (200, 201):
            print("[错误] 创建仓库失败：HTTP %s" % status)
            print(json.dumps(repo, ensure_ascii=False, indent=2)[:600])
            sys.exit(3)
        print("仓库创建成功：%s" % repo.get("html_url"))

    root = os.path.abspath(args.dir)
    files = collect(root)
    print("待上传 %d 个文件（已跳过 runtime / data / 缓存 / 压缩包）" % len(files))

    ok = 0
    for rel, full in files:
        with open(full, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        path = "/repos/%s/%s/contents/%s" % (
            urllib.parse.quote(owner), urllib.parse.quote(args.repo),
            urllib.parse.quote(rel))
        st, info = call("GET", path + "?ref=" + urllib.parse.quote(args.branch), token)
        payload = {"content": content, "message": "feat: 上传 %s" % rel,
                   "branch": args.branch}
        method = "POST"
        if st == 200 and isinstance(info, dict) and info.get("sha"):
            payload["sha"] = info["sha"]
            method = "PUT"
        st, res = call(method, path, token, payload)
        if st in (200, 201):
            ok += 1
            print("  成功 %-42s %s" % (rel, "更新" if method == "PUT" else "新增"))
        else:
            print("  失败 %-42s HTTP %s %s" % (rel, st, str(res)[:220]))
        time.sleep(0.25)

    html = "https://gitee.com/%s/%s" % (owner, args.repo)
    raw_cover = "https://gitee.com/%s/%s/raw/%s/cover.png" % (owner, args.repo,
                                                              args.branch)
    print("\n完成：%d/%d 个文件" % (ok, len(files)))
    print("仓库地址   ：%s" % html)
    print("封面图直链 ：%s" % raw_cover)

    time.sleep(1.5)
    for url in (html, raw_cover):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                print("匿名访问 %s → HTTP %s" % (url, resp.status))
        except urllib.error.HTTPError as exc:
            print("匿名访问 %s → HTTP %s" % (url, exc.code))
        except Exception as exc:
            print("匿名访问 %s → 失败 %s" % (url, exc))


if __name__ == "__main__":
    main()
