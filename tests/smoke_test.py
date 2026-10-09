"""离线冒烟测试：python tests/smoke_test.py [--online]

--online 会额外访问一次 https://example.com 验证网络层（可选）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studycrawler.config import Settings  # noqa: E402
from studycrawler.db import StateDB  # noqa: E402
from studycrawler.engines.forum import file_links_in_page, parse_list  # noqa: E402
from studycrawler.engines.linkhub import extract_links  # noqa: E402
from studycrawler.hunt import _ddg_real_url, _hits_from_selectors, suggest_type  # noqa: E402
from studycrawler.storage import category_dir, safe_filename  # noqa: E402

LIST_HTML = """
<html><body>
  <div class="post"><h3><a href="/t/101">Python 入门电子书</a></h3></div>
  <div class="post"><h3><a href="/t/102">C 语言速查</a></h3></div>
  <div class="post"><h3>没有链接的帖子</h3></div>
</body></html>
"""

DETAIL_HTML = """
<html><body>
  <a href="/files/python.pdf">点我下载</a>
  <a href="/files/python.pdf">重复链接</a>
  <a href="https://cdn.example.com/audio/lesson.mp4">视频</a>
  <a href="/page/next">下一页（不是文件）</a>
</body></html>
"""

LINK_HTML = """
<html><body>
  <div>信号与系统课程 提取码: x8k2
    <a href="https://pan.baidu.com/s/1AbcDef?pwd=x8k2">百度网盘</a>
  </div>
  <a href="https://pan.quark.cn/s/2xyz998">夸克合集 密码 ab12</a>
  <p>magnet:?xt=urn:btih:0123456789ABCDEF 纯文本种子</p>
</body></html>
"""


def test_safe_filename() -> None:
    bad = 'a<b>:"c|d?e/f\\g'
    good = safe_filename(bad)
    for ch in '\/:*?"<>|':
        assert ch not in good, f"仍含非法字符 {ch}: {good}"
    assert safe_filename("x.") == "x", "结尾的点应被去掉"
    assert safe_filename("") == "unnamed"
    print("ok  safe_filename")


def test_parse_list() -> None:
    items = parse_list(
        LIST_HTML, "https://example.com/list",
        {"item": "div.post", "title": "h3 a", "link": "h3 a"},
    )
    assert items == [
        ("Python 入门电子书", "https://example.com/t/101"),
        ("C 语言速查", "https://example.com/t/102"),
    ], items
    print("ok  forum.parse_list")


def test_file_links() -> None:
    links = file_links_in_page(DETAIL_HTML, "https://example.com/t/101")
    assert links == [
        "https://example.com/files/python.pdf",
        "https://cdn.example.com/audio/lesson.mp4",
    ], links
    print("ok  forum.file_links_in_page")


def test_extract_links() -> None:
    found = extract_links(LINK_HTML)
    by_host = {r["link"].split("/")[2] if "://" in r["link"] else "magnet": r
               for r in found}
    assert len(found) == 3, found
    baidu = by_host["pan.baidu.com"]
    assert baidu["code"] == "x8k2", baidu
    quark = by_host["pan.quark.cn"]
    assert quark["code"] == "ab12", quark
    magnets = [r for r in found if r["link"].startswith("magnet:")]
    assert magnets, "纯文本 magnet 未识别"
    print("ok  linkhub.extract_links")


def test_db() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = StateDB(Path(td) / "state.db")
        db.mark("http://a/1", "src", title="甲", status="done")
        assert db.seen("http://a/1", "src")
        assert not db.seen("http://a/1", "other"), "去重应按来源隔离"
        db.mark("http://a/2", "src", title="乙", status="error")
        assert not db.seen("http://a/2", "src"), "失败的条目下轮应重试"
        db.close()  # Windows 上不关连接的话临时目录删不掉
    print("ok  db.seen/mark")


def test_category_dir() -> None:
    with tempfile.TemporaryDirectory() as td:
        st = Settings(sync_dir=Path(td), db_path=Path(td) / "s.db")
        d = category_dir(st, "_hunt/AI绘画: 教程")
        assert d == Path(td) / "_hunt" / "AI绘画_ 教程", d
        assert d.is_dir()
    print("ok  storage.category_dir")


def test_hunt_parsers() -> None:
    bing_html = """
    <html><body><ol>
      <li class="b_algo"><h2><a href="https://a.com/1">AI 绘画教程</a></h2></li>
      <li class="b_algo"><h2><a href="https://b.com/2">Claude 提效</a></h2></li>
      <li class="b_algo"><h2>没链接</h2></li>
    </ol></body></html>
    """
    hits = _hits_from_selectors(bing_html, "https://cn.bing.com/search",
                                "li.b_algo", "h2 a", "h2 a")
    assert len(hits) == 2 and hits[0]["url"] == "https://a.com/1", hits

    # ddg 跳转链接解码
    real = _ddg_real_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fx&rut=1")
    assert real == "https://a.com/x", real
    assert _ddg_real_url("https://plain.com/y") == "https://plain.com/y"

    assert suggest_type("https://www.bilibili.com/video/BV1x") == "video"
    assert suggest_type("https://www.pixiv.net/users/1") == "gallery"
    assert suggest_type("https://some-forum.com/t/1") == "forum"
    print("ok  hunt.parsers")


def test_keywords_queue() -> None:
    from studycrawler.hunt import mark_query_done, query_done, read_keywords

    with tempfile.TemporaryDirectory() as td:
        kf = Path(td) / "keywords.txt"
        kf.write_text("# 注释\n板绘\n\n笔刷 教程\n", encoding="utf-8")
        assert read_keywords(kf) == ["板绘", "笔刷 教程"]
        assert read_keywords(Path(td) / "不存在.txt") == []

        db = StateDB(Path(td) / "s.db")

        class FakeApp:
            pass

        app = FakeApp()
        app.db = db
        assert not query_done(app, "板绘")
        mark_query_done(app, "板绘")
        assert query_done(app, "板绘")
        assert not query_done(app, "笔刷 教程")
        db.close()
    print("ok  hunt.keywords queue")


def test_strong_first() -> None:
    from studycrawler.hunt import _strong_first

    urls = [
        "https://x.com/a.jpg", "https://x.com/brush.zip",
        "https://x.com/pic.png", "https://x.com/book.pdf",
    ]
    ordered = _strong_first(urls)
    assert ordered[0].endswith(("brush.zip", "book.pdf")), ordered
    assert ordered[-1].endswith((".jpg", ".png")), ordered
    assert len(ordered) == 4
    print("ok  hunt.strong_first")


def test_pan_parsers() -> None:
    from studycrawler.hunt import _is_pan_link, _parse_pansearch_item

    content = (
        "名称：<span class='highlight-keyword'>板绘</span>教程合集 "
        "<a href=\"https://pan.baidu.com/s/1AbCde?pwd=x9k2\">网盘</a>"
    )
    hit = _parse_pansearch_item(content, "板绘")
    assert hit == {
        "title": "板绘", "url": "https://pan.baidu.com/s/1AbCde?pwd=x9k2", "code": "x9k2",
    }, hit
    assert _parse_pansearch_item("没有链接", "x") is None

    assert _is_pan_link("https://pan.baidu.com/s/1AbC")
    assert _is_pan_link("magnet:?xt=urn:btih:ABC123")
    assert not _is_pan_link("https://www.bilibili.com/video/BV1x")
    print("ok  hunt.pan parsers")


def test_checker() -> None:
    from studycrawler import checker

    class FF:
        def get_text(self, url):
            if "dead" in url:
                return "<html>分享的文件已经被取消，链接已失效</html>"
            return "<html>请输入提取码 share</html>"

        def post_json(self, url, body, headers=None, accept=(200,)):
            if body.get("pwd_id") == "dead1":
                return {"code": 41012, "message": "好友已取消了分享"}
            return {"code": 0}

    assert checker.check("https://pan.baidu.com/s/dead1", FF()) == (
        "dead", "分享的文件已经被取消")
    assert checker.check("https://pan.baidu.com/s/ok1", FF())[0] == "ok"
    assert checker.check("https://pan.quark.cn/s/dead1", FF()) == (
        "dead", "好友已取消了分享")
    assert checker.check("https://pan.quark.cn/s/ok1", FF())[0] == "ok"
    assert checker.check("https://www.alipan.com/s/x", FF())[0] == "unknown"
    assert checker.check("magnet:?xt=urn:btih:A1", FF())[0] == "unknown"

    assert checker.is_checkable("https://pan.baidu.com/s/x")
    assert checker.is_checkable("https://pan.quark.cn/s/x")
    assert checker.is_checkable("https://wwm.lanzouq.com/x")
    assert not checker.is_checkable("https://www.alipan.com/s/x")
    assert not checker.is_checkable("magnet:?xt=urn:btih:A1")

    line = "- 2026-10-09 10:31 | 板绘 | https://pan.quark.cn/s/abc  提取码 4gk2 [有效]"
    rec = checker.parse_line(line)
    assert rec["url"] == "https://pan.quark.cn/s/abc"
    assert rec["code"] == "4gk2"
    assert rec["status"] == "有效"
    assert checker.parse_line("# 标题行") is None
    assert checker.render_line({**rec, "status": "失效: 已过期"}).endswith(
        "[失效: 已过期]")
    print("ok  checker")


def test_online() -> None:
    from studycrawler.fetch import Fetcher

    with tempfile.TemporaryDirectory() as td:
        st = Settings(sync_dir=Path(td), db_path=Path(td) / "s.db")
        text = Fetcher(st).get_text("https://example.com")
        assert "Example Domain" in text
    print("ok  fetch.get_text (online)")


def main() -> None:
    test_safe_filename()
    test_parse_list()
    test_file_links()
    test_extract_links()
    test_db()
    test_category_dir()
    test_hunt_parsers()
    test_keywords_queue()
    test_strong_first()
    test_pan_parsers()
    test_checker()
    if "--online" in sys.argv:
        test_online()
    print("\n全部通过")


if __name__ == "__main__":
    main()
