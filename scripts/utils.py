# -*- coding: utf-8 -*-
"""
公共工具模块
认证、时间格式化、字数统计、图片统计
"""

import os
import re
import sys
import json
import logging
import platform
from datetime import datetime, timedelta, timezone

import github

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# 常量定义
TOP_ISSUES_LABELS = ["Top", "置顶"]
TODO_ISSUES_LABELS = ["TODO", "待办"]
IGNORE_LABELS = TOP_ISSUES_LABELS + TODO_ISSUES_LABELS + ["bug", "enhancement"]
POSTS_DIR = "posts"
POSTS_INDEX_FILE = "posts/index.json"
POSTS_EXPORT_FILE = "posts_export.json"
RECENT_ISSUE_LIMIT = 20
METADATA_FILE = ".temp_metadata.json"

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


def log_environment():
    """输出运行环境信息，用于跨环境调试"""
    env_info = {
        "platform": platform.platform(),
        "python_version": sys.version,
        "os_name": os.name,
        "is_ci": os.getenv("GITHUB_ACTIONS") == "true",
        "event_name": os.getenv("GITHUB_EVENT_NAME", "local"),
        "cwd": os.getcwd(),
        "encoding": sys.getdefaultencoding(),
        "filesystem_encoding": sys.getfilesystemencoding(),
    }
    logger.info(f"[ENV] {json.dumps(env_info, ensure_ascii=False)}")


def _normalize_line_endings(text):
    """归一化换行符：\\r\\n → \\n, \\r → \\n，确保跨平台一致"""
    if not text:
        return ""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _dedupe(items):
    """去重且保持原有顺序"""
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def get_me(user):
    """获取当前用户信息（返回登录名候选列表，兼容账号改名场景）

    账号改名后：issue 作者显示改后的新用户名，而 GITHUB_ACTOR（尤其
    schedule 自动触发）可能仍返回旧名，单字符串严格比较会全部失配，
    导致 README 文章列表/统计被清空。因此返回多候选：
    - GITHUB_REPOSITORY 的所有者：跟随仓库改名，恒为当前用户名，最可靠
    - GITHUB_ACTOR：触发事件的具体用户，作为补充（过滤机器人账号 [bot]）
    - 本地运行：token 对应的当前登录用户
    任一候选命中即视为本人（is_me 支持列表）。
    """
    try:
        if os.getenv("GITHUB_ACTIONS") == "true":
            candidates = []
            # 仓库所有者跟随账号改名，恒为当前用户名，比 GITHUB_ACTOR 更可靠
            repo_name = os.getenv("GITHUB_REPOSITORY", "")
            if repo_name and '/' in repo_name:
                owner = repo_name.split('/')[0]
                candidates.append(owner)
                logger.info(f"在GitHub Actions环境中，使用仓库所有者: {owner}")
            # GITHUB_ACTOR 是触发 action 的用户名，作为补充（排除机器人账号）
            actor = os.getenv("GITHUB_ACTOR", "")
            if actor and not actor.endswith("[bot]"):
                candidates.append(actor)
                logger.info(f"在GitHub Actions环境中，使用 GITHUB_ACTOR: {actor}")
            if candidates:
                return _dedupe(candidates)
            logger.info("在GitHub Actions环境中，使用默认用户名")
            return ["github-actions"]
        login_name = user.get_user().login
        logger.info(f"本地运行，使用当前登录用户: {login_name}")
        return [login_name]
    except Exception as e:
        logger.warning(f"获取当前用户信息失败，使用默认值: {str(e)}")
        return ["unknown_user"]


def is_me(issue_or_comment, me):
    """判断issue或评论是否属于自己（me 支持单个登录名或候选列表）"""
    try:
        user_login = issue_or_comment.user.login
        if isinstance(me, (list, tuple, set)):
            return user_login in me
        return user_login == me
    except Exception as e:
        logger.error(f"判断用户身份失败: {str(e)}")
        return False


def login(token):
    """登录GitHub"""
    try:
        try:
            import github.Auth
            auth = github.Auth.Token(token)
            return github.Github(auth=auth)
        except ImportError:
            logger.warning("使用旧版本的PyGithub认证方法")
            return github.Github(token)
    except Exception as e:
        logger.error(f"登录GitHub失败: {str(e)}")
        raise


def get_repo(user, repo_name):
    """获取GitHub仓库"""
    try:
        return user.get_repo(repo_name)
    except Exception as e:
        logger.error(f"获取仓库失败: {str(e)}")
        raise


def format_time(time_obj):
    """格式化时间为北京时间 (UTC+8)"""
    try:
        if not hasattr(time_obj, 'strftime'):
            return "未知时间"
        if time_obj.tzinfo is None:
            time_obj = time_obj.replace(tzinfo=timezone.utc)
        local_time = time_obj.astimezone(BEIJING_TZ)
        return local_time.strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        logger.error(f"格式化时间失败: {str(e)}")
        return "时间格式化失败"


def _clean_markdown(text):
    """去掉 markdown 语法，返回纯文本（跨平台兼容）"""
    if not text:
        return ""

    # 归一化换行符（消除 Windows/Linux 差异）
    text = _normalize_line_endings(text)

    # 移除 HTML 注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # 移除代码块
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'~~~.*?~~~', '', text, flags=re.DOTALL)
    # 移除行内代码
    text = re.sub(r'`[^`]*`', '', text)
    # 移除图片（在移除链接之前处理）
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    # 移除链接语法，保留链接文本 [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 移除 URL（裸链接）
    text = re.sub(r'https?://\S+', '', text)
    # 移除表格分隔线
    text = re.sub(r'^\s*\|?[-:| ]+\|?\s*$', '', text, flags=re.MULTILINE)
    # 移除引用标记、列表标记
    text = re.sub(r'^\s*[>\-*+]\s+', '', text, flags=re.MULTILINE)
    # 移除标题标记
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 移除水平分隔线
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # 移除加粗/斜体标记
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    # 移除删除线
    text = re.sub(r'~~([^~]+)~~', r'\1', text)
    # 移除表格管道符和多余的空白
    text = re.sub(r'\|', ' ', text)
    # 移除反斜杠转义
    text = re.sub(r'\\(.)', r'\1', text)
    # 合并多余的空白
    return re.sub(r'\s+', ' ', text).strip()


def _count_words(clean_text):
    """从纯文本中统计字数：中文单字 + 英文单词 + 数字"""
    if not clean_text:
        return 0
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', clean_text))
    english_words = len(re.findall(r'\b[a-zA-Z]+\b', clean_text))
    numbers = len(re.findall(r'\b\d+\b', clean_text))
    return chinese_chars + english_words + numbers


def get_content_word_count(content):
    """从文本内容统计字数（去掉 markdown 后真正显示的字符数）"""
    try:
        clean = _clean_markdown(content)
        result = _count_words(clean)
        logger.debug(f"[STAT] word_count={result}, clean_text_len={len(clean)}, raw_len={len(content) if content else 0}")
        return result
    except Exception as e:
        logger.error(f"从内容统计字数失败: {str(e)}")
        return 0


def get_content_image_count(content):
    """从文本内容统计图片数量（Markdown ![]() 和 HTML <img>）"""
    try:
        if not content:
            return 0
        md_images = len(re.findall(r'!\[[^\]]*\]\([^)]+\)', content))
        html_images = len(re.findall(r'<img[^>]+src=["\'][^"\']+["\']', content, re.IGNORECASE))
        result = md_images + html_images
        logger.debug(f"[STAT] image_count={result} (md={md_images}, html={html_images})")
        return result
    except Exception as e:
        logger.error(f"从内容统计图片数量失败: {str(e)}")
        return 0


def get_issue_word_count(issue):
    """获取 issue 的字数统计（仅统计 issue.body，不含评论）"""
    try:
        return _count_words(_clean_markdown(issue.body or ""))
    except Exception as e:
        logger.error(f"获取issue字数失败 #{issue.number}: {str(e)}")
        return 0


def get_issue_image_count(issue):
    """获取 issue 中的图片数量（仅统计 issue.body，不含评论）"""
    try:
        if not issue.body:
            return 0
        md_images = len(re.findall(r'!\[[^\]]*\]\([^)]+\)', issue.body))
        html_images = len(re.findall(r'<img[^>]+src=["\'][^"\']+["\']', issue.body, re.IGNORECASE))
        return md_images + html_images
    except Exception as e:
        logger.error(f"获取issue图片数量失败 #{issue.number}: {str(e)}")
        return 0


def is_pull_request(issue):
    """检查Issue对象是否实际上是一个Pull Request"""
    try:
        return issue.pull_request is not None
    except Exception:
        return False


def should_include_issue(issue, metadata=None):
    """检查issue是否应该出现在文章列表中
    条件：
    1. 不是 Pull Request
    2. 状态为 open
    3. 字数 > 0
    4. 插图数量 >= 0
    """
    if is_pull_request(issue):
        return False

    if issue.state != "open":
        return False

    # 检查字数：优先从元数据读取，回退到 issue.body
    if metadata and str(issue.number) in metadata:
        word_count = metadata[str(issue.number)].get("word_count", 0)
        image_count = metadata[str(issue.number)].get("image_count", 0)
    else:
        word_count = get_issue_word_count(issue)
        image_count = get_issue_image_count(issue)

    if word_count <= 0:
        return False

    if image_count < 0:
        return False

    return True


def load_metadata():
    """加载元数据文件，返回 issue_number -> metadata 的字典"""
    if not os.path.exists(METADATA_FILE):
        return {}
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载元数据失败: {str(e)}")
        return {}


def save_metadata(metadata):
    """保存元数据到文件"""
    try:
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存元数据失败: {str(e)}")