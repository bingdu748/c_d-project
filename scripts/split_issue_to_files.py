# -*- coding: utf-8 -*-
"""
按评论拆分 issue → 每个评论一个 .md 文件

目录结构：
  posts/<标签>/<issue标题>/            ← 文件夹以 issue 标题命名
    01_<评论一级标题>.md               ← 每条评论一个文件，序号保序
    02_<评论一级标题>.md
    ...
    index.md                          ← 自动生成的索引（序号/标题/中文/英文/图片/更新时间）

说明：
  - 幂等：重跑即用评论最新状态全量重建该目录（含 index.md），实现"同步修改+统计联动"
  - 仅处理 comments API 中属于自己且未被隐藏的评论
  - 文件名非法字符（/\\:*?"<>|）替换为 '-'
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import logging

from scripts.utils import (
    logger, login, get_repo, get_me, is_me, format_time,
    get_content_word_count, get_content_image_count,
    load_metadata, save_metadata,
    POSTS_DIR, IGNORE_LABELS,
)

# Windows 等文件系统非法字符
_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# 文件夹 slug 化需要去除的中文全角标点（引号、逗号、括号等）
_FULLWIDTH_TRIM = re.compile(r'[「」『』“”‘’【】（）《》〈〉、，。：；？！…·—\-]')

INDEX_NAME = "00_index.md"


def sanitize_name(name, max_len=80):
    """把标题转成安全文件名：替换非法字符，保留中文/全角/括号/点"""
    safe = _INVALID_FILENAME.sub('-', name or '').strip().strip('.')
    safe = re.sub(r'\s+', ' ', safe)
    return safe[:max_len]


def slugify_folder(title, max_len=80):
    """文件夹名 slug 化：去全角标点、空白转连字符，如「Diary」2026 → Diary-2026"""
    s = _FULLWIDTH_TRIM.sub('-', title or '')
    s = _INVALID_FILENAME.sub('-', s)
    s = s.replace(' ', '-')
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:max_len] or 'untitled'


def get_label_dir(issue):
    """与 generate_posts 一致：取非忽略标签，无则 no-label"""
    labels = [label.name for label in issue.labels]
    content_labels = [l for l in labels if l not in IGNORE_LABELS]
    return content_labels[0] if content_labels else "no-label"


def extract_title_and_body(comment):
    """取评论首行一级标题作为文档标题；无 H1 时退回发布时间

    提取 H1 后从正文中移除该行，避免文件里标题出现两遍。
    """
    body = comment.body or ""
    h1 = re.match(r'^#\s+(.+?)(?:\n|$)', body)
    if h1:
        title = h1.group(1).strip()
        # 去掉首行标题，其余正文原样保留（独立文件，内部标题不降级）
        remaining = body[h1.end():].strip()
        return title, remaining if remaining else "*(无内容)*"
    return format_time(comment.created_at), body


def remove_legacy_merged(issue, base_dir):
    """删除旧合并版 md（posts/<label>/*.md 中包含该 issue URL 的），结构升级后不残留"""
    label_path = os.path.join(base_dir, get_label_dir(issue))
    if not os.path.isdir(label_path):
        return
    url = issue.html_url
    for f in os.listdir(label_path):
        if not f.endswith('.md'):
            continue
        fp = os.path.join(label_path, f)
        try:
            with open(fp, 'r', encoding='utf-8') as fh:
                content = fh.read()
            if url in content:
                os.remove(fp)
                logger.info(f"清理旧合并版: {fp}")
        except Exception:
            pass


def _dir_belongs_to_issue(dir_path, issue):
    """判断目录是否属于该 issue（读 00_index.md 中的 issue 编号标记）"""
    index_file = os.path.join(dir_path, INDEX_NAME)
    if not os.path.exists(index_file):
        return False
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            return f"issue #{issue.number}" in f.read()
    except Exception:
        return False


def clean_legacy_dirs(issue, base_dir):
    """删除该 issue 旧标题遗留的目录（同标签下、属于同一 issue 但 slug 不同的旧目录）

    issue 标题改名后，新的 slug 目录已由 split_issue 生成，这里清理残留。
    """
    label_path = os.path.join(base_dir, get_label_dir(issue))
    if not os.path.isdir(label_path):
        return
    current = slugify_folder(issue.title)
    for name in os.listdir(label_path):
        if name == current:
            continue
        candidate = os.path.join(label_path, name)
        if not os.path.isdir(candidate):
            continue
        if _dir_belongs_to_issue(candidate, issue):
            import shutil
            shutil.rmtree(candidate)
            logger.info(f"清理旧标题遗留目录: {candidate}")


def split_issue(issue, me, base_dir):
    """拆分单个 issue：建目录、每评论一个 md、生成 index.md、写 metadata"""
    label_dir = get_label_dir(issue)
    issue_dir = os.path.join(base_dir, label_dir, slugify_folder(issue.title))
    os.makedirs(issue_dir, exist_ok=True)

    # 清理该 issue 旧标题遗留的目录（标题改名场景）
    clean_legacy_dirs(issue, base_dir)

    # 清理上次生成的拆分文件（幂等重建）
    for f in os.listdir(issue_dir):
        if f.endswith('.md'):
            os.remove(os.path.join(issue_dir, f))

    comments = [c for c in issue.get_comments()
                if is_me(c, me) and not getattr(c, 'minimized', False)]
    comments.sort(key=lambda c: c.created_at)

    stats = []    # (标题, 文件名, 字数, 图片, 更新时间)
    total_word = get_content_word_count(issue.body or "")
    total_image = get_content_image_count(issue.body or "")
    for idx, c in enumerate(comments, 1):
        title, body = extract_title_and_body(c)
        fname = f"{idx:02d}_{sanitize_name(title)}.md"
        fpath = os.path.join(issue_dir, fname)

        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(f"# {title}\n\n")
            f.write(body.rstrip() + "\n")

        content = open(fpath, encoding='utf-8').read()
        wc = get_content_word_count(content)
        ic = get_content_image_count(content)
        total_word += wc
        total_image += ic
        stats.append((title, fname, wc, ic, format_time(c.updated_at)))
        logger.info(f"  [{idx:02d}] {fname} 字数={wc} 图片={ic}")

    # index.md 索引表
    with open(os.path.join(issue_dir, INDEX_NAME), 'w', encoding='utf-8') as f:
        f.write(f"# {issue.title}\n\n")
        f.write(f"> 来源：[issue #{issue.number}]({issue.html_url}) | "
                f"评论数 {len(stats)} | 生成于 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("| 标题 | 字数 | 图片 | 更新时间 |\n")
        f.write("| :--: | :-: | :--: | :--: |\n")
        for title, fname, wc, ic, up in stats:
            f.write(f"| [{title}]({fname}) | {wc} | {ic} | {up} |\n")
        f.write("\n> 口径：字数=去 Markdown 后中文+英文+数字总数，图片=Markdown 图片数。重跑脚本即全量刷新。\n")

    # 评论明细（含时间戳，供 README 按篇统计新增/更新）
    comment_meta = [
        {
            "title": title,
            "created_at": c.created_at.isoformat() if hasattr(c.created_at, 'isoformat') else str(c.created_at),
            "updated_at": c.updated_at.isoformat() if hasattr(c.updated_at, 'isoformat') else str(c.updated_at),
        }
        for (title, _fname, _wc, _ic, _up), c in zip(stats, comments)
    ]

    # 写入 .temp_metadata.json（键与旧格式兼容，供 update_readme/README 统计）
    metadata = load_metadata()
    metadata[str(issue.number)] = {
        "title": issue.title,
        "filename": slugify_folder(issue.title),
        "label": label_dir,
        "updated": issue.updated_at.isoformat() if hasattr(issue.updated_at, 'isoformat') else str(issue.updated_at),
        "word_count": total_word,
        "image_count": total_image,
        "split_dir": issue_dir,
        "comment_count": len(comment_meta),
        "comments": comment_meta,
    }
    save_metadata(metadata)

    # 删除旧合并版，避免 README 按旧文件回退到过期统计
    remove_legacy_merged(issue, base_dir)

    logger.info(f"完成: {issue_dir}（{len(stats)} 条评论，字数 {total_word}，图片 {total_image}）")
    return issue_dir


def delete_issue(issue, base_dir):
    """issue 关闭时：删除拆分目录与旧合并版，清除 metadata"""
    label_dir = get_label_dir(issue)
    issue_dir = os.path.join(base_dir, label_dir, slugify_folder(issue.title))
    if os.path.isdir(issue_dir):
        import shutil
        shutil.rmtree(issue_dir)
        logger.info(f"删除拆分目录: {issue_dir}")
    remove_legacy_merged(issue, base_dir)
    metadata = load_metadata()
    if str(issue.number) in metadata:
        del metadata[str(issue.number)]
        save_metadata(metadata)
    logger.info(f"Issue #{issue.number} 已清理")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="按评论拆分 issue 为多个 md 文件")
    parser.add_argument("token", help="GitHub Personal Access Token")
    parser.add_argument("repo_name", help="仓库名称 (owner/repo)")
    parser.add_argument("--issue_number", type=int, default=None, help="指定 issue 编号（不指定则处理全部 open issue）")
    args = parser.parse_args()

    user = login(args.token)
    me = get_me(user)
    repo = get_repo(user, args.repo_name)
    logging.getLogger().setLevel(logging.INFO)

    if args.issue_number:
        issue = repo.get_issue(args.issue_number)
        if issue.state == "closed":
            delete_issue(issue, POSTS_DIR)
            return
        issues = [issue]
    else:
        issues = [i for i in repo.get_issues(state="open")
                  if is_me(i, me) and i.pull_request is None]

    for issue in issues:
        try:
            split_issue(issue, me, POSTS_DIR)
        except Exception as e:
            logger.error(f"拆分 issue #{issue.number} 失败: {e}")


if __name__ == "__main__":
    main()