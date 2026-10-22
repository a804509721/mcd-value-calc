#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 WorkBuddy 会话记录（JSONL）导出开发对话为 Markdown。

WorkBuddy 把每个会话按行存成 JSONL，路径形如：
    ~/.workbuddy/projects/<工作目录slug>/<会话UUID>.jsonl

每行是一条记录，type 取值包括：
    session-meta / message / reasoning / function_call /
    function_call_result / file-history-snapshot / ai-title

本脚本只取 message、function_call、function_call_result 三类，
按时间戳排序还原成可读的对话流水；reasoning（内部推理）只计数不导出原文。

用法：
    python tools/export_conversation.py                          # 导出当前目录最近一次会话
    python tools/export_conversation.py --list                   # 列出本机所有会话
    python tools/export_conversation.py --session a.jsonl b.jsonl -o workbuddy.md
    python tools/export_conversation.py --project d-workbuddy-2026-10-22-10-17-30
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".workbuddy", "projects")

# 需要从用户消息里剔除的系统注入块
RE_SYSTEM_BLOCK = re.compile(r"<system-reminder\b[^>]*>.*?</system-reminder>", re.S)
RE_TASK_NOTIFY = re.compile(r"<task-notification>.*?</task-notification>", re.S)
RE_CB_SUMMARY = re.compile(r"<cb_summary>.*?</cb_summary>", re.S)
RE_USER_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.S)
RE_TAG_LINE = re.compile(r"^\s*</?[a-zA-Z_][\w\-]*[^>]*>\s*$")

# 导出前必须脱敏：全局替换后的 Markdown 再不会出现明文凭据
RE_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}")
RE_SECRET_KEYS = re.compile(
    r"(?i)\b(token|api[_-]?key|apikey|secret|password|passwd|authorization)\b"
    r"(\s*[:=]\s*[\"']?)([A-Za-z0-9._\-]{12,})"
)
# 已知的明文 token（形如 32 位随机串），出现即抹掉
RE_LONG_OPAQUE = re.compile(r"\b(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{32}\b")
# 历史遗留的假邮箱（含旧托管平台域名），不应出现在公开仓库
RE_LEGACY_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@gitee\.(?:local|com)", re.I)
# 本机家目录用户名（JSON 里反斜杠会被转义成双份，所以分隔符允许一个或多个）
_USER_RE = re.escape(os.path.basename(HOME))
RE_HOME_USER = re.compile(r"([A-Za-z]:[\\/]+Users[\\/]+)" + _USER_RE, re.I)
# ls -l 输出里的 uid 列
RE_BARE_USER = re.compile(r"(?<![\w])" + _USER_RE + r"(?![\w])")


def redact(text):
    if not text:
        return text
    text = RE_BEARER.sub(r"\1<已脱敏>", text)
    text = RE_SECRET_KEYS.sub(r"\1\2<已脱敏>", text)
    text = RE_LONG_OPAQUE.sub("<已脱敏>", text)
    text = RE_LEGACY_EMAIL.sub("<旧邮箱已脱敏>", text)
    text = RE_HOME_USER.sub(r"\1<user>", text)
    text = RE_BARE_USER.sub("<user>", text)
    return text


def read_jsonl(path):
    events = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def strip_system_noise(text):
    """去掉 WorkBuddy 注入的 system-reminder / task-notification / 压缩摘要块。"""
    if not text:
        return ""
    if "<cb_summary>" in text:
        return ""  # 上下文压缩摘要，不是用户发言
    if "<task-notification>" in text:
        # 后台任务回调是系统自动插入的，后面跟的那句也不是用户说的
        return ""
    text = RE_SYSTEM_BLOCK.sub("", text)
    m = RE_USER_QUERY.search(text)
    if m:
        text = m.group(1)
    else:
        text = text.replace("<user_query>", "").replace("</user_query>", "")
    lines = [ln for ln in text.split("\n") if not RE_TAG_LINE.match(ln)]
    return "\n".join(lines).strip()


def blocks_of(msg):
    content = msg.get("content")
    return content if isinstance(content, list) else []


def text_of(msg, block_type="input_text"):
    out = []
    for b in blocks_of(msg):
        if b.get("type") == block_type and b.get("text"):
            out.append(b["text"])
    return "\n".join(out)


def strip_reasoning_prefix(text):
    """部分用户消息前面会粘上模型内部推理的尾巴，这里只做保守清理。"""
    return text


def fmt_ts(ms):
    try:
        return datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M")
    except (OSError, ValueError, TypeError):
        return "--:--"


def compact(s, limit):
    if s is None:
        return ""
    # 先脱敏再截断：否则凭据被截成半截后正则匹配不上，反而留下残缺明文
    s = redact(str(s)).replace("\r", "")
    s = re.sub(r"\n{2,}", "\n", s).strip()
    s = s.replace("\n", " ⏎ ")
    if len(s) > limit:
        s = s[:limit] + " …（已截断）"
    return s


def collect(files):
    """把多个会话文件的记录合并成一条按时间排序的事件流。

    工具结果按 callId 回填到对应的工具调用上，避免时间线里出现孤立的输出块。
    """
    timeline = []
    results = {}
    starts = {}
    for idx, path in enumerate(files):
        for ev in read_jsonl(path):
            ts0 = ev.get("timestamp") or 0
            if ts0:
                starts[idx] = min(starts.get(idx, ts0), ts0)
            t = ev.get("type")
            if t == "message":
                role = ev.get("role")
                if role == "user":
                    raw = text_of(ev, "input_text")
                    clean = strip_system_noise(raw)
                    if not clean:
                        continue
                    has_img = any(b.get("type") == "input_image" for b in blocks_of(ev))
                    if has_img:
                        clean += "\n\n（附图）"
                    timeline.append((ts0, "user", clean, idx))
                elif role == "assistant":
                    reply = text_of(ev, "output_text").strip()
                    if reply:
                        timeline.append((ts0, "assistant", reply, idx))
            elif t == "function_call":
                args = ev.get("arguments")
                if isinstance(args, (dict, list)):
                    args = json.dumps(args, ensure_ascii=False)
                timeline.append(
                    (
                        ts0,
                        "tool_call",
                        (ev.get("name", "?"), args or "", ev.get("callId", "")),
                        idx,
                    )
                )
            elif t == "function_call_result":
                output = ev.get("output") or {}
                body = output.get("text") if isinstance(output, dict) else str(output)
                results[ev.get("callId", "")] = (body or "", ev.get("status", ""))
            elif t == "reasoning":
                timeline.append((ts0, "reasoning", None, idx))
    timeline.sort(key=lambda x: x[0])
    return timeline, results, starts


def render(timeline, results, starts=None, arg_limit=260, result_limit=520):
    users = sum(1 for e in timeline if e[1] == "user")
    replies = sum(1 for e in timeline if e[1] == "assistant")
    calls = sum(1 for e in timeline if e[1] == "tool_call")
    thinks = sum(1 for e in timeline if e[1] == "reasoning")

    out = []
    out.append("# WorkBuddy 开发对话记录\n")
    out.append(
        "> 本文件由 `tools/export_conversation.py` 从 WorkBuddy 会话记录（JSONL）自动导出，\n"
        "> 用于核验本项目确实使用 WorkBuddy 开发。原始记录未做内容修改，\n"
        "> 仅去除系统提示块，并对过长的工具输出做截断（标注「已截断」）。\n"
    )
    out.append("| 项目 | 数量 |")
    out.append("|---|---|")
    out.append("| 用户发言 | %d |" % users)
    out.append("| WorkBuddy 回复 | %d |" % replies)
    out.append("| 工具调用 | %d |" % calls)
    out.append("| 内部推理段（仅计数，未导出原文） | %d |" % thinks)
    out.append("")
    out.append("---")
    out.append("")

    prev_src = None
    for ts, kind, payload, src in timeline:
        if kind == "reasoning":
            continue
        stamp = fmt_ts(ts)
        if src != prev_src:
            if prev_src is not None and starts:
                out.append("> ——— 会话 %d 起（%s） ———\n" % (src + 1, fmt_ts(starts.get(src, ts))))
            prev_src = src
        if kind == "user":
            out.append("### 👤 用户 · %s\n" % stamp)
            out.append(redact(payload))
            out.append("")
        elif kind == "assistant":
            out.append("### 🤖 WorkBuddy · %s\n" % stamp)
            out.append(redact(payload))
            out.append("")
        elif kind == "tool_call":
            name, args, call_id = payload
            out.append("- 🔧 **%s**" % name)
            out.append("  - 参数：`%s`" % compact(args, arg_limit))
            body, status = results.get(call_id, ("", ""))
            if status == "error":
                out.append("  - ⚠️ 报错：`%s`" % compact(body, result_limit))
            elif body.strip():
                out.append("  - 结果：`%s`" % compact(body, result_limit))
    return redact("\n".join(out) + "\n")


def list_sessions(projects_dir=PROJECTS_DIR, limit=25):
    rows = []
    if not os.path.isdir(projects_dir):
        return rows
    for proj in os.listdir(projects_dir):
        pdir = os.path.join(projects_dir, proj)
        if not os.path.isdir(pdir):
            continue
        for fn in os.listdir(pdir):
            if not fn.endswith(".jsonl"):
                continue
            full = os.path.join(pdir, fn)
            st = os.stat(full)
            rows.append((st.st_mtime, st.st_size, proj, full))
    rows.sort(reverse=True)
    return rows[:limit]


def newest_session(projects_dir=PROJECTS_DIR):
    rows = list_sessions(projects_dir, limit=1)
    return rows[0][3] if rows else None


def main():
    ap = argparse.ArgumentParser(description="从 WorkBuddy 会话 JSONL 导出 Markdown 对话记录")
    ap.add_argument("--session", nargs="+", help="一个或多个会话 JSONL 路径")
    ap.add_argument("--project", help="会话目录名（~/.workbuddy/projects/ 下的目录）")
    ap.add_argument("--list", action="store_true", help="列出本机会话")
    ap.add_argument("-o", "--out", default=None, help="输出文件，默认打印到 stdout")
    ap.add_argument("--arg-limit", type=int, default=260, help="工具参数截断长度")
    ap.add_argument("--result-limit", type=int, default=520, help="工具结果截断长度")
    args = ap.parse_args()

    if args.list:
        for mtime, size, proj, full in list_sessions():
            stamp = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            print("%s  %8.1fKB  %-38s %s" % (stamp, size / 1024.0, proj, os.path.basename(full)))
        return 0

    files = []
    if args.session:
        files = args.session
    elif args.project:
        pdir = os.path.join(PROJECTS_DIR, args.project)
        if not os.path.isdir(pdir):
            print("找不到会话目录：%s" % pdir, file=sys.stderr)
            return 1
        files = [
            os.path.join(pdir, fn) for fn in sorted(os.listdir(pdir)) if fn.endswith(".jsonl")
        ]
    else:
        one = newest_session()
        if not one:
            print("没找到任何会话记录，请用 --session 指定。", file=sys.stderr)
            return 1
        files = [one]

    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        print("文件不存在：%s" % ", ".join(missing), file=sys.stderr)
        return 1

    timeline, results, starts = collect(files)
    md = render(timeline, results, starts, args.arg_limit, args.result_limit)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        print(
            "已导出 %d 个会话 → %s（%.1f KB）"
            % (len(files), args.out, os.path.getsize(args.out) / 1024.0)
        )
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
