# -*- coding: utf-8 -*-
"""
文章生成模块
从 GitHub Issues 拉取数据，生成 .md 文件到 posts/ 目录
按标签分目录，无标签放 no-label/
"""

import os
import sys
# 将项目根目录加入 sys.path，使脚本可直接 python scripts/xxx.py 运行
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import re
import json
import glob
import logging
from datetime import datetime, timezone
from scripts.utils import (
    logger, login, get_repo, get_me, is_me, format_time,
    get_content_word_count, get_content_image_count,
    POSTS_DIR, POSTS_INDEX_FILE, POSTS_EXPORT_FILE,
    IGNORE_LABELS, METADATA_FILE, load_metadata, save_metadata,
    log_environment
)


def get_to_generate_issues(repo, me, issue_number=None):
    """获取需要生成的issue列表"""
    try:
        if issue_number:
            logger.info(f"指定了issue_number: {issue_number}，将只处理该issue")
            try:
                return [repo.get_issue(int(issue_number))]
            except Exception as e:
                logger.error(f"获取指定issue失败: {str(e)}")
                return []

        logger.info("未指定issue_number，将处理所有issue")
        return list(repo.get_issues())
    except Exception as e:
        logger.error(f"获取待生成的issues失败: {str(e)}")
        return []


def sanitize_filename(title):
    """将标题转换为安全的文件名"""
    safe = title.replace('/', '-').replace('\\', '-').replace(' ', '.')
    safe = ''.join(c for c in safe if c.isalnum() or c in '.-_')
    return safe


def get_label_dir(issue):
    """获取 issue 应存放的标签目录名"""
    labels = [label.name for label in issue.labels]
    content_labels = [l for l in labels if l not in IGNORE_LABELS]
    return content_labels[0] if content_labels else "no-label"


def _extract_comment_title_and_body(comment, me):
    """从评论中提取标题和正文，标题降级一级"""
    body = comment.body or "*(无内容)*"

    # 尝试提取评论首行的一级标题作为分段标题
    h1_match = re.match(r'^#\s+(.+?)(?:\n|$)', body)
    if h1_match:
        title = h1_match.group(1).strip()
        # 去掉首行标题，其余内容所有标题降一级
        remaining = body[h1_match.end():].strip()
        return title, _downgrade_headings(remaining) if remaining else "*(无内容)*"

    # 没有 H1，使用时间戳作为标题，全部内容降级
    time_str = format_time(comment.created_at)
    return time_str, _downgrade_headings(body)


def _downgrade_headings(text):
    """将文本中所有 # 标题降一级（# → ##, ## → ### ... ###### → #######）"""
    return re.sub(r'^(#{1,6})(?=\s)', r'#\1', text, flags=re.MULTILINE)


def save_issue(issue, me):
    """保存 issue 为 .md 文件到 posts/ 目录下"""
    label_dir = get_label_dir(issue)
    dir_path = os.path.join(POSTS_DIR, label_dir)
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
        logger.info(f"创建标签目录: {dir_path}")

    safe_title = sanitize_filename(issue.title)
    md_path = os.path.join(dir_path, f"{safe_title}.md")

    try:
        with open(md_path, "w", encoding="utf-8") as f:
            # 元数据注释块（机器可读，不影响渲染）
            labels = [l.name for l in issue.labels]
            f.write("<!--\n")
            f.write(f"  issue_number: {issue.number}\n")
            f.write(f"  state: {issue.state}\n")
            f.write(f"  created_at: {issue.created_at.isoformat() if hasattr(issue.created_at, 'isoformat') else str(issue.created_at)}\n")
            f.write(f"  updated_at: {issue.updated_at.isoformat() if hasattr(issue.updated_at, 'isoformat') else str(issue.updated_at)}\n")
            f.write(f"  labels: [{', '.join(labels)}]\n")
            f.write(f"  url: {issue.html_url}\n")
            f.write("-->\n\n")

            # 一级标题：issue 标题（链接回原文）
            f.write(f"# [{issue.title}]({issue.html_url})\n\n")

            # 文档说明：issue 正文
            f.write("## 文档说明\n\n")
            f.write(issue.body or "*(无内容)*")
            f.write("\n")

            # 评论：每个评论作为独立分段，按时间顺序排列
            comments = list(issue.get_comments())
            my_comments = [c for c in comments if is_me(c, me)]
            if my_comments:
                # 按创建时间排序
                my_comments.sort(key=lambda c: c.created_at)
                logger.info(f"处理issue #{issue.number} 的 {len(my_comments)} 条评论")

                for c in my_comments:
                    title, body = _extract_comment_title_and_body(c, me)
                    f.write(f"\n## {title}\n\n")
                    f.write(body)
                    f.write("\n")

        logger.info(f"保存issue文件: {md_path}")

        # 读取完整文件内容，统计字数与图片数（包含正文+评论）
        with open(md_path, "r", encoding="utf-8") as f:
            full_content = f.read()
        word_count = get_content_word_count(full_content)
        image_count = get_content_image_count(full_content)

        # 更新元数据
        metadata = load_metadata()
        metadata[str(issue.number)] = {
            "title": issue.title,
            "filename": safe_title,
            "label": label_dir,
            "updated": issue.updated_at.isoformat() if hasattr(issue.updated_at, 'isoformat') else str(issue.updated_at),
            "word_count": word_count,
            "image_count": image_count
        }
        save_metadata(metadata)
        logger.info(f"issue #{issue.number} 字数: {word_count}, 图片: {image_count}")

        return md_path
    except Exception as e:
        logger.error(f"保存issue #{issue.number} 到 {md_path} 失败: {str(e)}")
        raise


def delete_issue_files(issue):
    """删除指定 issue 的所有 .md 文件"""
    try:
        # 先尝试从元数据中查找
        metadata = load_metadata()
        issue_key = str(issue.number)

        if issue_key in metadata:
            info = metadata[issue_key]
            old_path = os.path.join(POSTS_DIR, info["label"], f"{info['filename']}.md")
            if os.path.exists(old_path):
                os.remove(old_path)
                logger.info(f"删除旧issue文件: {old_path}")

        # 遍历所有目录确保清理干净（处理元数据丢失的情况）
        for root, dirs, files in os.walk(POSTS_DIR):
            for f in files:
                if f.endswith('.md'):
                    filepath = os.path.join(root, f)
                    try:
                        with open(filepath, "r", encoding="utf-8") as fh:
                            first_line = fh.readline()
                            # 通过文件名中的标题匹配（标题已 sanitize）
                            safe_title = sanitize_filename(issue.title)
                            if safe_title in f:
                                os.remove(filepath)
                                logger.info(f"删除旧issue文件(遍历): {filepath}")
                    except Exception:
                        pass

        # 从元数据中移除
        if issue_key in metadata:
            del metadata[issue_key]
            save_metadata(metadata)

        logger.info(f"Issue #{issue.number} 的文件已清理")
    except Exception as e:
        logger.error(f"删除issue文件失败 #{issue.number}: {str(e)}")


def cleanup_empty_dirs():
    """清理 posts/ 下的空目录"""
    try:
        for root, dirs, files in os.walk(POSTS_DIR, topdown=False):
            if root == POSTS_DIR:
                continue
            if not os.listdir(root):
                os.rmdir(root)
                logger.info(f"删除空目录: {root}")
    except Exception as e:
        logger.error(f"清理空目录失败: {str(e)}")


def export_json(repo_name):
    """生成结构化 JSON 导出文件 posts_export.json
    包含所有 issue 的完整元数据 + 内容，便于程序化处理和数据迁移
    """
    try:
        metadata = load_metadata()
        if not metadata:
            logger.warning("元数据为空，跳过 JSON 导出")
            return

        export = {
            "repository": repo_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_issues": len(metadata),
            "issues": []
        }

        for issue_number, info in sorted(metadata.items(), key=lambda x: int(x[0])):
            issue_entry = {
                "issue_number": int(issue_number),
                "title": info.get("title", ""),
                "filename": info.get("filename", ""),
                "label": info.get("label", "no-label"),
                "updated": info.get("updated", ""),
                "word_count": info.get("word_count", 0),
                "image_count": info.get("image_count", 0),
                "url": f"https://github.com/{repo_name}/issues/{issue_number}"
            }

            # 新结构：按评论拆分到 posts/<label>/<issue标题slug>/ 目录
            # 旧结构：单文件 posts/<label>/<filename>.md（兼容历史数据）
            md_path = None
            split_dir = info.get("split_dir")
            if split_dir and os.path.isdir(split_dir):
                issue_entry["md_path"] = split_dir
                # 拼接该 issue 目录下全部 .md（含 00_index.md 与评论文件）
                md_files = sorted(
                    os.path.join(split_dir, f)
                    for f in os.listdir(split_dir)
                    if f.endswith(".md")
                )
                try:
                    content_parts = []
                    for fp in md_files:
                        with open(fp, "r", encoding="utf-8") as f:
                            content_parts.append(f.read())
                    issue_entry["content"] = "\n\n".join(content_parts)
                except Exception as e:
                    logger.warning(f"读取 {split_dir} 失败: {e}")
                    issue_entry["content"] = ""
            else:
                md_path = os.path.join(
                    POSTS_DIR, info.get("label", "no-label"),
                    f"{info.get('filename', '')}.md"
                )
                issue_entry["md_path"] = md_path
                if os.path.exists(md_path):
                    try:
                        with open(md_path, "r", encoding="utf-8") as f:
                            issue_entry["content"] = f.read()
                    except Exception as e:
                        logger.warning(f"读取 {md_path} 失败: {e}")
                        issue_entry["content"] = ""
                else:
                    issue_entry["content"] = ""

            export["issues"].append(issue_entry)

        with open(POSTS_EXPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON 导出完成: {POSTS_EXPORT_FILE} ({len(metadata)} 个 issue)")
    except Exception as e:
        logger.error(f"JSON 导出失败: {str(e)}")


def generate_index_json():
    """生成 posts/ 目录索引 posts/index.json
    包含所有 .md 文件的路径、标题、标签、字数等元数据
    """
    try:
        import hashlib

        index = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": []
        }

        if not os.path.isdir(POSTS_DIR):
            logger.warning(f"posts/ 目录不存在，跳过索引生成")
            return

        for root, dirs, files in os.walk(POSTS_DIR):
            # 跳过隐藏目录
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in sorted(files):
                if not f.endswith('.md'):
                    continue
                filepath = os.path.join(root, f)
                rel_path = os.path.relpath(filepath, POSTS_DIR)
                label_dir = os.path.dirname(rel_path) or "root"

                try:
                    with open(filepath, "r", encoding="utf-8") as fh:
                        content = fh.read()

                    # 提取标题（第一个 H1）
                    title_match = re.match(r'^#\s+(.+?)(?:\n|$)', content)
                    title = title_match.group(1).strip() if title_match else f

                    wc = get_content_word_count(content)
                    ic = get_content_image_count(content)

                    # 计算文件 SHA256（用于完整性校验）
                    file_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]

                    index["entries"].append({
                        "path": rel_path,
                        "title": title,
                        "label": label_dir,
                        "word_count": wc,
                        "image_count": ic,
                        "file_size": len(content.encode('utf-8')),
                        "sha256_short": file_hash
                    })
                except Exception as e:
                    logger.warning(f"索引 {filepath} 失败: {e}")

        index["total_files"] = len(index["entries"])

        with open(POSTS_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        logger.info(f"索引生成完成: {POSTS_INDEX_FILE} ({len(index['entries'])} 个文件)")
    except Exception as e:
        logger.error(f"索引生成失败: {str(e)}")


def main():
    """主入口：生成 .md 文件"""
    import argparse

    parser = argparse.ArgumentParser(description="从 GitHub Issues 生成 .md 文件")
    parser.add_argument("token", help="GitHub Personal Access Token")
    parser.add_argument("repo_name", help="仓库名称 (owner/repo)")
    parser.add_argument("--issue_number", type=int, default=None, help="指定 issue 编号")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("开始生成 issue .md 文件")
    log_environment()

    # 登录
    user = login(args.token)
    me = get_me(user)
    logger.info(f"登录成功: {me}")

    # 获取仓库
    repo = get_repo(user, args.repo_name)

    # 处理指定 issue（关闭的 issue 执行删除）
    if args.issue_number:
        try:
            issue = repo.get_issue(args.issue_number)
            if issue.state == "closed":
                logger.info(f"Issue #{args.issue_number} 已关闭，删除文件")
                delete_issue_files(issue)
                cleanup_empty_dirs()
                return
        except Exception as e:
            logger.error(f"获取 issue #{args.issue_number} 失败: {str(e)}")

    # 获取需要生成的 issues
    issues = get_to_generate_issues(repo, me, args.issue_number)
    logger.info(f"需要处理 {len(issues)} 个 issue")

    if not issues:
        logger.info("没有需要处理的 issue")
        return

    # 生成 .md 文件
    for issue in issues:
        if not is_me(issue, me):
            logger.debug(f"跳过非自己的 issue: #{issue.number}")
            continue
        try:
            save_issue(issue, me)
        except Exception as e:
            logger.error(f"处理 issue #{issue.number} 失败: {str(e)}")

    cleanup_empty_dirs()

    # 生成 JSON 结构化导出和目录索引
    export_json(args.repo_name)
    generate_index_json()

    logger.info("issue .md 文件生成完成")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()