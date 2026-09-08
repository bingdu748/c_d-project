# -*- coding: utf-8 -*-
"""
README 更新模块
生成 README.md、feed.xml，统计字数/图片/更新时间
"""

import os
import sys
# 将项目根目录加入 sys.path，使脚本可直接 python scripts/xxx.py 运行
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import logging
from datetime import datetime, timedelta, timezone

from scripts.utils import (
    logger, login, get_repo, get_me, is_me, format_time,
    get_issue_word_count, get_issue_image_count, load_metadata,
    is_pull_request, should_include_issue,
    count_from_md_file, log_environment,
    TOP_ISSUES_LABELS, TODO_ISSUES_LABELS, IGNORE_LABELS,
    RECENT_ISSUE_LIMIT, BEIJING_TZ
)


def _has_label(issue, labels):
    """判断 issue 是否包含给定标签列表中的任意一个"""
    try:
        issue_labels = [l.name for l in issue.labels]
        return any(l in issue_labels for l in labels)
    except Exception:
        return False


def _add_issue_line(issue):
    """单条issue的Markdown行（返回字符串，不直接写文件）"""
    time = format_time(issue.updated_at)
    return f"- [{issue.title}]({issue.html_url})--{time}\n"


def add_md_todo(all_issues, me):
    """生成待办事项的Markdown字符串（内存过滤）"""
    try:
        todo_issues = [i for i in all_issues if _has_label(i, TODO_ISSUES_LABELS)]
        if not TODO_ISSUES_LABELS or not todo_issues:
            logger.debug("没有找到待办标签或待办文章")
            return ""
        todo_issues = sorted(todo_issues, key=lambda x: x.updated_at, reverse=True)
        logger.debug(f"找到 {len(todo_issues)} 个待办文章")

        lines = ["## 待办事项\n"]
        for issue in todo_issues:
            if is_me(issue, me):
                lines.append(_add_issue_line(issue))
        return "".join(lines)
    except Exception as e:
        logger.error(f"添加待办事项部分失败: {str(e)}")
        raise


def add_md_top(all_issues, me):
    """生成置顶文章的Markdown字符串（内存过滤）"""
    try:
        top_issues = [i for i in all_issues if _has_label(i, TOP_ISSUES_LABELS)]
        if not TOP_ISSUES_LABELS or not top_issues:
            logger.debug("没有找到Top标签或置顶文章")
            return ""
        top_issues = sorted(top_issues, key=lambda x: x.updated_at, reverse=True)
        logger.debug(f"找到 {len(top_issues)} 个置顶文章")

        lines = ["## 置顶文章\n"]
        for issue in top_issues:
            if is_me(issue, me):
                lines.append(_add_issue_line(issue))
        return "".join(lines)
    except Exception as e:
        logger.error(f"添加置顶文章部分失败: {str(e)}")
        raise


def add_md_recent(all_issues, me, limit=RECENT_ISSUE_LIMIT):
    """生成文章列表的Markdown字符串"""
    try:
        lines = ["## 文章列表\n", "| 序号 | 文章标题 | 更新时间 | 字数统计 | 插图统计 |\n",
                 "|:------:|:------------------:|:------------------:|:------:|:------:|\n"]
        count = 0
        logger.debug("获取所有issue并按更新时间排序...")
        all_issues = sorted(all_issues, key=lambda x: x.updated_at, reverse=True)
        logger.debug(f"获取到 {len(all_issues)} 个issue")

        # 加载元数据（含生成 .md 文件时计算的完整字数/图片数）
        metadata = load_metadata()

        for issue in all_issues:
            if is_me(issue, me) and should_include_issue(issue, metadata):
                time = format_time(issue.updated_at)

                # 三层回退：.md 文件（含评论全文）→ 元数据缓存 → issue.body（无评论）
                # 优先 .md 文件：它由 generate_posts 实时生成，比元数据缓存更不易过期
                issue_key = str(issue.number)
                word_count = None
                image_count = None
                source = "unknown"

                wc, ic = count_from_md_file(issue.number, issue.title)
                if wc is not None:
                    word_count = wc
                    image_count = ic
                    source = "md_file"
                    logger.info(f"[STAT_SRC] #{issue.number} 使用 .md 文件: wc={word_count}, ic={image_count}")

                if word_count is None and issue_key in metadata and "word_count" in metadata[issue_key]:
                    word_count = metadata[issue_key]["word_count"]
                    image_count = metadata[issue_key].get("image_count", 0)
                    source = "metadata"
                    logger.debug(f"[STAT_SRC] #{issue.number} 使用元数据: wc={word_count}, ic={image_count}")

                if word_count is None:
                    word_count = get_issue_word_count(issue)
                    image_count = get_issue_image_count(issue)
                    source = "issue_body"
                    logger.warning(f"[STAT_SRC] #{issue.number} 回退到 issue.body: wc={word_count}, ic={image_count}")

                lines.append(
                    f"| {count + 1} | [{issue.title}]({issue.html_url}) "
                    f"| {time} | {word_count} | {image_count} |\n"
                )
                count += 1
                if count >= limit:
                    break
        logger.debug(f"已添加 {count} 个最近更新的issue")
        return "".join(lines)
    except Exception as e:
        logger.error(f"添加最近更新部分失败: {str(e)}")
        raise


def add_md_label(all_issues, labels, me):
    """生成标签分类的Markdown字符串（内存过滤）"""
    try:
        labels = sorted(
            labels,
            key=lambda x: (
                x.description is None,
                x.description == "",
                x.description,
                x.name,
            ),
        )

        lines = []
        for label in labels:
            if label.name in IGNORE_LABELS:
                continue

            # 内存过滤，不再对每个标签单独发 API 请求
            issues_list = [i for i in all_issues if label.name in [l.name for l in i.labels]]
            if not issues_list:
                continue

            lines.append(f"## {label.name}\n")
            issues_list = sorted(issues_list, key=lambda x: x.updated_at, reverse=True)
            logger.debug(f"标签 '{label.name}' 下有 {len(issues_list)} 个issue")

            i = 0
            for issue in issues_list:
                if not issue:
                    continue
                if is_me(issue, me):
                    lines.append(_add_issue_line(issue))
                    i += 1
            if i > 0:
                lines.append("\n")
        return "".join(lines)
    except Exception as e:
        logger.error(f"添加标签分类部分失败: {str(e)}")
        raise


def generate_changelog(all_issues, me):
    """生成 CHANGELOG.md — 记录非本人的 PR（Dependabot 等），独立于 README"""
    try:
        # 筛选非本人的 PR
        bot_prs = [issue for issue in all_issues if not is_me(issue, me) and is_pull_request(issue)]
        bot_prs = sorted(bot_prs, key=lambda x: x.updated_at, reverse=True)

        if not bot_prs:
            logger.debug("没有找到第三方 PR，跳过 CHANGELOG 生成")
            return

        with open("CHANGELOG.md", "w", encoding="utf-8") as f:
            f.write("# 更新日志\n\n")
            f.write("> 本文件由自动化工作流生成，记录第三方提交的 Pull Request（如 Dependabot）。\n\n")
            f.write("| PR 标题 | 链接 | 更新时间 |\n")
            f.write("|:--------|:-----|:--------|\n")
            for pr in bot_prs[:RECENT_ISSUE_LIMIT]:
                time = format_time(pr.updated_at)
                f.write(f"| {pr.title} | [PR #{pr.number}]({pr.html_url}) | {time} |\n")

        logger.info(f"CHANGELOG.md 生成成功，共 {len(bot_prs[:RECENT_ISSUE_LIMIT])} 条 PR")
    except Exception as e:
        logger.error(f"生成 CHANGELOG.md 失败: {str(e)}")


def generate_rss_feed(all_issues, repo, me):
    """生成RSS feed文件"""
    try:
        all_issues = sorted(
            [issue for issue in all_issues if is_me(issue, me) and not is_pull_request(issue)],
            key=lambda x: x.updated_at, reverse=True
        )

        rss_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
    <title>{repo.name} Blog</title>
    <link>{repo.html_url}</link>
    <description>Blog generated from GitHub issues</description>
    <language>zh-CN</language>
    <lastBuildDate>{datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
"""

        for issue in all_issues[:RECENT_ISSUE_LIMIT]:
            pub_date = issue.updated_at.strftime('%a, %d %b %Y %H:%M:%S GMT')
            body = issue.body or ""
            description = (body[:200] + '...') if len(body) > 200 else body
            rss_content += f"""
    <item>
        <title>{issue.title}</title>
        <link>{issue.html_url}</link>
        <description>{description}</description>
        <pubDate>{pub_date}</pubDate>
        <guid>{issue.html_url}</guid>
    </item>
"""

        rss_content += """
</channel>
</rss>
"""

        with open("feed.xml", "w", encoding="utf-8") as f:
            f.write(rss_content)

        logger.info("RSS feed生成成功")
    except Exception as e:
        logger.error(f"生成RSS feed失败: {str(e)}")
        raise


def ensure_readme_exists():
    """确保README.md文件存在"""
    if os.path.exists("README.md"):
        return
    logger.warning("README.md文件不存在，创建默认README.md")
    try:
        default_content = """# My GitHub Blog

欢迎访问我的GitHub Blog！本博客基于GitHub Issues构建。

## 最近更新

（暂无内容，请创建Issue）
"""
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(default_content)
        logger.info("已创建默认的README.md")
    except Exception as e:
        logger.error(f"创建README.md失败: {str(e)}")
        raise


def regenerate_readme(repo, repo_name, me):
    """重新生成README.md文件（内存拼装，内容不变则不落盘）"""
    try:
        log_environment()
        logger.info("开始重新生成README.md...")

        # 一次性拉取全部 issue 与标签，后续各模块内存过滤，避免重复 API 请求
        all_issues = list(repo.get_issues(state='all'))
        all_labels = list(repo.get_labels())
        logger.info(f"一次性拉取 {len(all_issues)} 个 issue, {len(all_labels)} 个标签")

        # 在内存拼装完整内容：开头简介 + 各区块 + 博客统计
        parts = []
        parts.append("> 记录自我，留存活过的痕迹。\n>\n")
        parts.append("> 人总想留下痕迹，证明自己活过。信息时代里，被数字化的东西只会越来越多——数字不会风化，也不会被遗忘。人有两次死亡：第一次是肉体，第二次是被遗忘。我选择把自己的日记与思考搬来这里，只为让第二次死亡来得晚一些。\n\n")

        parts.append(add_md_top(all_issues, me))
        parts.append(add_md_todo(all_issues, me))
        # 标签分类区块已停用：README 只保留文章列表 + 博客统计（与 master 现状一致）
        # parts.append(add_md_label(all_issues, all_labels, me))
        parts.append(add_md_recent(all_issues, me))

        # 生成 CHANGELOG.md（第三方 PR 独立文档）
        generate_changelog(all_issues, me)

        # 统计信息
        beijing_now = datetime.now(BEIJING_TZ)
        update_time = beijing_now.strftime("%Y-%m-%d %H:%M:%S")

        my_issues = [issue for issue in all_issues if is_me(issue, me) and not is_pull_request(issue)]
        total_articles = len(my_issues)

        # 优先从元数据读取（含评论），回退到仅统计 issue.body
        metadata = load_metadata()
        total_word_count = 0
        total_image_count = 0
        for issue in my_issues:
            issue_key = str(issue.number)
            if issue_key in metadata and "word_count" in metadata[issue_key]:
                total_word_count += metadata[issue_key]["word_count"]
                total_image_count += metadata[issue_key].get("image_count", 0)
            else:
                total_word_count += get_issue_word_count(issue)
                total_image_count += get_issue_image_count(issue)

        # 最近24小时内的新增和更新
        recent_threshold = beijing_now - timedelta(hours=24)
        recent_updated = [
            issue for issue in my_issues
            if issue.updated_at.replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ) > recent_threshold
        ]
        recent_created = [
            issue for issue in my_issues
            if issue.created_at.replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ) > recent_threshold
        ]

        parts.append("\n\n## 博客统计\n")
        parts.append(f"- 最后更新: {update_time}\n")
        parts.append(f"- 总文章数: {total_articles}\n")
        parts.append(f"- 新增文章: {len(recent_created)}\n")
        parts.append(f"- 更新文章: {len(recent_updated)}\n")
        parts.append(f"- 总字数: {total_word_count}\n")
        parts.append(f"- 总插图数: {total_image_count}\n")

        new_content = "".join(parts)

        # 与现有内容对比，仅在有变更时落盘，避免无谓触碰文件
        if os.path.exists("README.md"):
            with open("README.md", "r", encoding="utf-8") as f:
                old_content = f.read()
        else:
            old_content = None

        if new_content == old_content:
            logger.info("README.md 内容无变化，跳过写入")
            return all_issues

        with open("README.md", "w", encoding="utf-8") as f:
            f.write(new_content)

        logger.info("README.md 重新生成完成")
        return all_issues
    except Exception as e:
        logger.error(f"重新生成README.md失败: {str(e)}")
        raise


def main():
    """主入口：生成 README.md 和 feed.xml"""
    import argparse

    parser = argparse.ArgumentParser(description="更新 README.md 和 feed.xml")
    parser.add_argument("token", help="GitHub Personal Access Token")
    parser.add_argument("repo_name", help="仓库名称 (owner/repo)")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("开始更新 README.md 和 feed.xml")

    # 登录
    user = login(args.token)
    me = get_me(user)
    logger.info(f"登录成功: {me}")

    # 获取仓库
    repo = get_repo(user, args.repo_name)

    # 确保 README.md 存在
    ensure_readme_exists()

    # 重新生成 README.md（复用同一次拉取的 issue 数据生成 RSS feed）
    all_issues = regenerate_readme(repo, args.repo_name, me)

    # 生成 RSS feed（复用已拉取的 issues，避免重复 API 请求）
    generate_rss_feed(all_issues, repo, me)

    logger.info("README.md 和 feed.xml 更新完成")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()