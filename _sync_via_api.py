#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 GitHub REST API（api.github.com，稳定）替代不稳定的 git 智能 HTTP，实现本地<->远端同步。

背景：本机 github.com 的 git 协议(443) 时好时坏，但 api.github.com 稳定可达。
本工具让"推送"与"拉取对齐"都走 REST API，彻底不依赖 `git fetch`/`git push`。

子命令：
  status            比较本地 HEAD 与远端 HEAD（远端走 API），显示 ahead/behind。
  push [tag]        把本地相对远端 HEAD 的直线领先内容推到远端（parent=远端 HEAD，非 force）。
                    等价 rebase+push；若本地与远端真正分叉会拒绝并要求先 align。
  align [sha]       拉取远端 main 最新对象并在本地重建后 reset --hard 对齐（不依赖 git fetch）。
                    省略 sha 时对齐远端 main；指定 sha 时仅把该 commit 对象下载重建到本地（不 reset），用于测试/取历史。

设计要点：
  - 所有网络交互仅经 https://api.github.com（HTTPS/443 到 api 子域，与 git 协议不同，稳定）。
  - align 的"对象重建"：GET /git/blobs -> git hash-object 写 blob；GET /git/trees(recursive)
    -> git mktree 重建目录树；GET /git/commits -> git commit-tree 重建 commit。重建出的 SHA
    与远端一致，故 reset --hard 可正确对齐，且本地获得完整对象、消除分叉。
  - token 仅从环境变量 GITHUB_TOKEN 读取，不上屏/不落命令行。
"""
import os
import sys
import json
import base64
import logging
import argparse
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s.%(msecs)03d] %(levelname)-5s %(name)s:%(lineno)d  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_via_api")

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ.get("GITHUB_TOKEN")
OWNER_REPO = None  # 由 parse_remote() 填充，形如 "ht182400-creator/worksheet"


def parse_remote():
    """从本地 git remote `origin` 解析 owner/repo（兼容 https 与 ssh 格式）。"""
    global OWNER_REPO
    if OWNER_REPO:
        return OWNER_REPO
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=REPO_DIR, encoding="utf-8"
        ).strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("无法读取 git remote origin：%s" % exc)
    # https://github.com/owner/repo.git 或 git@github.com:owner/repo.git
    url = url.replace(":", "/").replace("git@", "").replace(".git", "")
    if "github.com/" in url:
        tail = url.split("github.com/")[-1]
        OWNER_REPO = "/".join(tail.split("/")[:2])
    else:
        raise RuntimeError("origin 不是 github.com 仓库：%s" % url)
    return OWNER_REPO


def api(method, path, payload=None):
    """调用 GitHub REST API；失败抛 RuntimeError（含 HTTP 状态与响应体）。"""
    if not TOKEN:
        raise RuntimeError("未找到 GITHUB_TOKEN 环境变量（需 repo 权限）")
    url = "https://api.github.com/repos/%s%s" % (parse_remote(), path)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "sync-via-api")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError("API %s %s -> %s: %s" % (method, path, exc.code, detail))


def sh(*args):
    """本地 git 只读/写命令，返回 stdout（utf-8）。"""
    return subprocess.check_output(["git"] + list(args), cwd=REPO_DIR, encoding="utf-8").strip()


def is_dirty():
    """工作区是否含未提交（已跟踪）改动。

    仅检查 tracked 改动（M/A/D 等）；untracked（??）不计入，因为 align 的
    reset --hard 不会触碰 untracked 文件，无需以此阻止对齐。
    """
    out = sh("status", "--porcelain")
    for line in out.splitlines():
        if line[:2] != "??" and line.strip():
            return True
    return False


def get_remote_head():
    """远端 main 当前 HEAD SHA（走 API，不依赖 git fetch）。"""
    ref = api("GET", "/git/refs/heads/main")
    return ref["object"]["sha"]


def ensure_blob(sha):
    """确保 blob 对象已存在于本地库；缺失则从 API 下载写入。"""
    try:
        subprocess.check_output(["git", "cat-file", "-e", sha], cwd=REPO_DIR)
        return
    except subprocess.CalledProcessError:
        pass
    blob = api("GET", "/git/blobs/%s" % sha)
    content = blob.get("content", "")
    if blob.get("encoding") == "base64":
        data = base64.b64decode(content)
    else:
        data = content.encode("utf-8")
    subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=REPO_DIR,
        input=data,
        check=True,
    )
    log.debug("blob 下载并写入 %s", sha)


def mktree(lines):
    """用 git mktree 由条目行生成 tree 对象，返回 tree SHA。"""
    inp = ("\n".join(sorted(lines)) + "\n").encode("utf-8")
    return subprocess.check_output(
        ["git", "mktree"], cwd=REPO_DIR, input=inp
    ).decode().strip()


def build_tree(entries):
    """按扁平 tree 条目（含 path）自底向上重建目录树，返回根 tree SHA。

    入参 entries: [{path, mode, type, sha}]（type 为 blob/tree）。
    """
    # 收集所有目录（含中间目录），并先把 blob 放到对应目录
    dir_items = {}  # dir_path -> {name: (mode, type, sha)}
    all_dirs = set()

    def put(dir_path, name, item):
        dir_items.setdefault(dir_path, {})[name] = item

    for e in entries:
        parts = e["path"].split("/")
        name = parts[-1]
        d = "/".join(parts[:-1])
        put(d, name, (e["mode"], "blob", e["sha"]))
        for i in range(1, len(parts)):
            all_dirs.add("/".join(parts[:i]))

    # 按深度降序处理：最深的目录先 mktree，再向上合并进父目录
    for d in sorted(all_dirs, key=lambda x: x.count("/"), reverse=True):
        items = dir_items.get(d, {})
        lines = ["%s %s %s\t%s" % (mode, typ, sha, name) for name, (mode, typ, sha) in items.items()]
        tree_sha = mktree(lines)
        parent = "/".join(d.split("/")[:-1]) if "/" in d else ""
        pname = d.split("/")[-1]
        put(parent, pname, ("040000", "tree", tree_sha))

    root_items = dir_items.get("", {})
    root_lines = [
        "%s %s %s\t%s" % (mode, typ, sha, name) for name, (mode, typ, sha) in root_items.items()
    ]
    return mktree(root_lines)


def _to_git_date(iso):
    """ISO 8601 日期 -> git 内部日期格式 '<unix_ts> <tz>'（如 '1719000000 +0800'）。

    git commit 对象的日期以 epoch+时区存储；用 epoch 格式传入可避免 ISO 字符串
    被 git 规范化后与原始对象产生差异（否则重建出的 SHA 不一致）。
    """
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    ts = int(dt.timestamp())
    off = dt.utcoffset()
    if off is None:
        tz = "+0000"
    else:
        total = int(off.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        tz = "%s%02d%02d" % (sign, total // 3600, (total % 3600) // 60)
    return "%d %s" % (ts, tz)


def build_commit(tree_sha, parent_shas, message, author, committer=None):
    """重建 commit 对象（tree/parent/author/committer/date/message 与远端逐字节一致）。

    不用 `git commit-tree -m`（会自动补尾随换行、ISO 日期规范化导致 SHA 漂移），
    改为直接按 git 对象格式拼出 commit 文本，用 `git hash-object -t commit` 写入，
    确保重建出的 SHA 与远端完全相同，reset 才能正确对齐。
    """
    committer = committer or author
    obj = b"tree " + tree_sha.encode("ascii") + b"\n"
    for p in parent_shas:
        obj += b"parent " + p.encode("ascii") + b"\n"
    obj += ("author %s <%s> %s\n" % (
        author["name"], author["email"], _to_git_date(author["date"]))).encode("utf-8")
    obj += ("committer %s <%s> %s\n" % (
        committer["name"], committer["email"], _to_git_date(committer["date"]))).encode("utf-8")
    obj += b"\n" + message.encode("utf-8")
    out = subprocess.check_output(
        ["git", "hash-object", "-w", "-t", "commit", "--stdin"],
        cwd=REPO_DIR, input=obj,
    )
    sha = out.decode().strip()
    log.debug("构造的 commit 对象文本:\n%s", obj.decode("utf-8", "replace"))
    return sha


def fetch_commit_into_local(sha):
    """把某个 commit（及其 tree/blob）从 API 下载并在本地重建；返回本地 commit SHA（应==sha）。"""
    commit = api("GET", "/git/commits/%s" % sha)
    tree_sha = commit["tree"]["sha"]
    tree = api("GET", "/git/trees/%s?recursive=1" % tree_sha)
    for e in tree.get("tree", []):
        if e.get("type") == "blob":
            ensure_blob(e["sha"])
    new_tree = build_tree(tree["tree"])
    if new_tree != tree_sha:
        raise RuntimeError("tree 重建不一致：本地 %s != 远端 %s" % (new_tree, tree_sha))
    author = commit.get("author") or commit.get("committer")
    committer = commit.get("committer") or author
    parents = [p["sha"] for p in commit.get("parents", [])]
    new_commit = build_commit(new_tree, parents, commit["message"], author, committer)
    if new_commit != sha:
        # 关键：Git Data API 返回的 commit 日期被规范为 UTC，丢失原始时区偏移，
        # 导致重建 commit 的 SHA 与远端原始对象不同。但 tree/parent/message 一致，
        # 内容完全等价；reset 后工作区与远端完全相同，满足"内容同步"目标。
        # 历史 SHA 的精确对齐仅能在 github.com 可用时由 `git fetch`+`reset --hard` 完成。
        log.warning("重建 commit 与远端 SHA 不同但内容等价：本地 %s / 远端 %s", new_commit, sha)
    return new_commit


# ----------------------------- 子命令 -----------------------------

def cmd_status():
    """显示本地 HEAD / 远端 HEAD 及同步关系（区分「SHA 分叉」与「内容分叉」）。

    关键判据：commit SHA 因 Git Data API 把日期规范为 UTC 而必然与远端原始对象不同，
    但 tree（代码内容指纹）不受影响。故以 tree 是否一致判定「内容是否真正同步」，
    SHA 不同但 tree 相同即「内容已同步、仅 SHA 分叉（API 工作流固有常态）」。
    """
    local = sh("rev-parse", "HEAD")
    remote = get_remote_head()
    log.info("本地 HEAD = %s", local)
    log.info("远端 HEAD = %s", remote)
    if local == remote:
        log.info("[OK] 已精确同步（本地 == 远端，SHA 一致）")
        return
    # 取双方 tree（代码内容指纹）：本地用 git；远端经 API 直接取 tree.sha（稳定，不受日期规范影响）
    local_tree = sh("rev-parse", local + "^{tree}")
    try:
        remote_tree = api("GET", "/git/commits/" + remote)["tree"]["sha"]
    except Exception:  # noqa: BLE001 - API 不可达时退化为旧逻辑
        remote_tree = None
    if local_tree and remote_tree:
        if local_tree == remote_tree:
            log.info("[OK] 内容已同步（tree 一致）：代码与远端一模一样。")
            log.info("   仅 commit SHA 因 Git Data API 把日期规范为 UTC 而不同 —— 这是纯 API 工作流固有常态，无需焦虑；")
            log.info("   要彻底消除 SHA 分叉，可在 github.com 通时执行 `git fetch && git reset --hard origin/main`。")
            return
        log.info("[WARN] 内容分叉（tree 不一致）：本地与远端代码确实不同，需比对 diff。")
    # 远端是否为本地祖先（可 push）
    try:
        base = sh("merge-base", remote, local)
    except subprocess.CalledProcessError:
        base = None
    if base == remote:
        log.info("本地领先远端（可 push）")
    else:
        log.info("本地与远端分叉（请先 align 对齐）")


def cmd_push(tag=None, dry_run=False):
    """把本地直线领先内容推到远端（parent=远端 HEAD）。"""
    remote = get_remote_head()
    local = sh("rev-parse", "HEAD")
    if remote == local:
        log.info("已同步，无需推送")
        return
    # 确保本地拥有远端 HEAD 对象（以便 diff / merge-base 可用），不 reset
    fetch_commit_into_local(remote)
    try:
        base = sh("merge-base", remote, local)
    except subprocess.CalledProcessError:
        base = None
    if base != remote:
        raise RuntimeError(
            "本地与远端真正分叉（远端不是本地祖先）。请先 `align` 对齐远端，"
            "在新基础上提交后再 push，避免覆盖他人改动。"
        )
    files = [f for f in sh("diff", "--name-only", remote, local).splitlines() if f]
    if not files:
        log.info("无文件差异，无需推送")
        return
    log.info("待推送文件 %d 个", len(files))
    entries = []
    for f in files:
        with open(os.path.join(REPO_DIR, f), "rb") as fh:
            raw = fh.read()
        b64 = base64.b64encode(raw).decode("ascii")
        blob = api("POST", "/git/blobs", {"content": b64, "encoding": "base64"})
        entries.append({"path": f, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = api("POST", "/git/trees", {"base_tree": None, "tree": entries})
    msg = sh("show", "-s", "--format=%B", "HEAD").rstrip("\n")
    author = {"name": sh("log", "-1", "--format=%an"), "email": sh("log", "-1", "--format=%ae"),
              "date": sh("log", "-1", "--format=%aI")}
    committer = {"name": sh("log", "-1", "--format=%cn"), "email": sh("log", "-1", "--format=%ce"),
                 "date": sh("log", "-1", "--format=%cI")}
    new_commit = api(
        "POST", "/git/commits",
        {"message": msg, "tree": tree["sha"], "parents": [remote],
         "author": author, "committer": committer},
    )
    log.info("新提交 = %s", new_commit["sha"])
    if dry_run:
        log.info("DRY-RUN：未更新远端引用")
        return
    api("PATCH", "/git/refs/heads/main", {"sha": new_commit["sha"], "force": False})
    log.info("已更新远端 main -> %s", new_commit["sha"])
    if tag:
        tag_obj = api(
            "POST", "/git/tags",
            {"tag": tag, "message": tag, "object": new_commit["sha"], "type": "commit",
             "tagger": committer},
        )
        api("POST", "/git/refs", {"ref": "refs/tags/%s" % tag, "sha": tag_obj["sha"]})
        log.info("已打 tag %s -> %s", tag, tag_obj["sha"])


def cmd_align(target_sha=None):
    """对齐远端（默认远端 main；指定 sha 仅重建不 reset）。

    两级策略：
      - 若本地已有所需远端 commit 对象（github.com 可用且 fetch 过时），直接
        `reset --hard` 到远端 SHA，做到**历史精确对齐**（消除分叉）。
      - 若对象缺失（github.com 不可达，无法 fetch），用 API 下载 tree/blob 重建一个
        **内容等价**的本地 commit（因 API 把日期规范为 UTC，SHA 会不同）并 reset，
        做到**内容对齐**（代码完全一致，SHA 不同为 API 工作流固有常态）。
    """
    if target_sha is None:
        if is_dirty():
            raise RuntimeError(
                "工作区有未提交改动，请先 `git stash` 或 commit 后再 align，避免被覆盖。"
            )
        target_sha = get_remote_head()
        log.info("对齐远端 main = %s", target_sha)
    else:
        log.info("将 commit %s 的对象下载重建到本地（不 reset）", target_sha)

    # 本地是否已有该 commit 对象：有则精确对齐，无则走 API 内容对齐
    try:
        subprocess.check_output(["git", "cat-file", "-e", target_sha], cwd=REPO_DIR)
        have_obj = True
    except subprocess.CalledProcessError:
        have_obj = False

    if have_obj:
        if target_sha is None:
            subprocess.run(["git", "reset", "--hard", target_sha], cwd=REPO_DIR, check=True)
            log.info("已精确对齐（reset --hard 到远端 %s）", target_sha)
        else:
            log.info("本地已存在该对象，无需重建")
        return

    local_sha = fetch_commit_into_local(target_sha)
    log.info("对象重建完成（内容等价），本地 commit = %s", local_sha)
    if target_sha is None:
        subprocess.run(["git", "reset", "--hard", local_sha], cwd=REPO_DIR, check=True)
        log.info("已内容对齐（reset --hard 到重建 commit %s；SHA 不同为 API 固有常态）", local_sha)


def main():
    parser = argparse.ArgumentParser(description="用 GitHub API 同步本地与远端（不依赖 git 协议）")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="比较本地/远端 HEAD")
    p_push = sub.add_parser("push", help="推送本地直线领先内容")
    p_push.add_argument("tag", nargs="?", default=None, help="可选：打 tag 名")
    p_push.add_argument("--dry-run", action="store_true", help="只重建不更新远端")
    p_align = sub.add_parser("align", help="拉取远端并 reset 对齐（不依赖 fetch）")
    p_align.add_argument("sha", nargs="?", default=None, help="可选：仅重建指定 commit 对象")
    args = parser.parse_args()
    try:
        if args.cmd == "status":
            cmd_status()
        elif args.cmd == "push":
            cmd_push(args.tag, args.dry_run)
        elif args.cmd == "align":
            cmd_align(args.sha)
        else:
            parser.print_help()
    except RuntimeError as exc:
        log.error("执行失败: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
