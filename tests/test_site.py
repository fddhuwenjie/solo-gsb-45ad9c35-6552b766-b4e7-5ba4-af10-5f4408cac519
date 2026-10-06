"""/site 站点读取的绝对站内 href 改写回归测试。

回归背景：发布含 ``<a href="/docs/guide.html?lang=zh#intro">`` 的 /index.html
与真实目标页后（发布校验通过），从 /site/index.html 点击该链接会请求
/docs/guide.html——不在 /site 挂载之下，按现有路由返回 404。
现在 /site 返回的 HTML 中绝对站内 href 指向 /site 下的同名路径，查询串与
片段仍指向原目标；相对链接、纯片段、纯查询、外部链接与非链接 HTML 保持原样；
/pages 正文与历史修订快照不受读取改写影响。

运行：pytest tests/test_site.py
"""

from __future__ import annotations

import os
import re
import tempfile

import pytest

# 必须在导入 app 之前指定数据库文件
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DOCS_DB_PATH"] = _TMP.name

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.site import rewrite_html_for_site  # noqa: E402


@pytest.fixture(scope="module")
def client():
    # 与其他测试模块隔离：直接改用本模块专属的数据库文件
    db.DB_PATH = _TMP.name
    db.init_db()
    with TestClient(app) as c:
        yield c


INDEX_HTML = (
    '<html><body>\n'
    '<h1 id="top">首页</h1>\n'
    '<a href="/docs/guide.html?lang=zh#intro">指南（绝对）</a>\n'
    '<a href="docs/guide.html#intro">指南（相对）</a>\n'
    '<a href="#top">纯片段</a>\n'
    '<a href="?lang=en">纯查询</a>\n'
    '<a href="https://example.com/x">外链</a>\n'
    '<a href="//cdn.example.com/lib.js">协议相对</a>\n'
    '<a href="mailto:a@b.com">邮件</a>\n'
    '<a href="/">站点根</a>\n'
    '<img src="/docs/guide.html">\n'
    '<!-- <a href="/docs/guide.html">注释里的链接</a> -->\n'
    '<p>正文提到 /docs/guide.html 不改</p>\n'
    '</body></html>'
)

GUIDE_HTML = (
    '<h2 id="intro">Guide</h2>'
    '<a href="../index.html#top">home rel</a>'
    '<a href="/index.html#top">home abs</a>'
)


# ---------- 改写纯函数 ----------

def test_rewrite_unit_only_absolute_insite_hrefs():
    html = (
        '<a href="/a.html?x=1&amp;y=2#f">abs</a>'
        '<a href="b.html">rel</a>'
        '<a href="../c.html">rel up</a>'
        '<a href="#f">frag</a>'
        '<a href="?q=1">query</a>'
        '<a href="">empty</a>'
        '<a href="https://ext.com/a.html">ext</a>'
        '<a href="//cdn.example/x">proto-rel</a>'
        '<a href="mailto:a@b.com">mail</a>'
        '<a href="/">root</a>'
        '<a>no href</a>'
    )
    out = rewrite_html_for_site(html)
    # 绝对站内 href 加 /site 前缀，查询串（含 HTML 实体）与片段原文保留
    assert '<a href="/site/a.html?x=1&amp;y=2#f">abs</a>' in out
    assert '<a href="/site/">root</a>' in out
    # 其余一律原样
    for kept in (
        '<a href="b.html">rel</a>',
        '<a href="../c.html">rel up</a>',
        '<a href="#f">frag</a>',
        '<a href="?q=1">query</a>',
        '<a href="">empty</a>',
        '<a href="https://ext.com/a.html">ext</a>',
        '<a href="//cdn.example/x">proto-rel</a>',
        '<a href="mailto:a@b.com">mail</a>',
        '<a>no href</a>',
    ):
        assert kept in out


def test_rewrite_unit_preserves_quoting_and_non_link_html():
    html = (
        "<a  href = '/a.html' >x</a>"
        '<a href= /b.html >y</a>'
        '<!-- <a href="/c.html">comment</a> -->'
        '<link href="/d.css" rel="stylesheet">'
    )
    out = rewrite_html_for_site(html)
    assert "href = '/site/a.html'" in out  # 单引号与等号两侧空白保留
    assert "href= /site/b.html" in out    # 无引号形式
    assert '<!-- <a href="/c.html">comment</a> -->' in out  # 注释不动
    assert '<link href="/d.css" rel="stylesheet">' in out   # 非 <a> 标签不动


def test_rewrite_unit_ignores_fake_href_inside_other_attributes():
    """回归：其他属性值里的伪 href= 不得被改写，只动真正的 href。

    历史缺陷：``title="hint href='/docs/bait.html'"`` 中的 ``href=``
    被误当作 href 属性加上 ``/site`` 前缀，真正的
    ``href="/docs/guide.html?lang=zh#intro"`` 反而原样保留，浏览器点击落到
    ``/site`` 之外而 404。
    """
    # title 用双引号、内嵌单引号伪 href（题述精确形态）
    html = "<a title=\"hint href='/docs/bait.html'\" href=\"/docs/guide.html?lang=zh#intro\">指南</a>"
    out = rewrite_html_for_site(html)
    assert out == (
        "<a title=\"hint href='/docs/bait.html'\" "
        'href="/site/docs/guide.html?lang=zh#intro">指南</a>'
    )
    # 反向引号嵌套：title 单引号、内嵌双引号伪 href
    html = "<a title='hint href=\"/docs/bait.html\"' href='/docs/guide.html?lang=zh#intro'>指南</a>"
    out = rewrite_html_for_site(html)
    assert out == (
        "<a title='hint href=\"/docs/bait.html\"' "
        "href='/site/docs/guide.html?lang=zh#intro'>指南</a>"
    )
    # 真 href 在前；data-href 与 title 中的伪 href= 都不动
    html = (
        '<a href="/docs/guide.html?lang=zh#intro" '
        "data-href=\"/docs/bait.html\" title=\"x href='/docs/bait.html'\">x</a>"
    )
    out = rewrite_html_for_site(html)
    assert 'href="/site/docs/guide.html?lang=zh#intro"' in out
    assert 'data-href="/docs/bait.html"' in out
    assert "title=\"x href='/docs/bait.html'\"" in out
    # 无引号真 href 也必须被识别（此前正则/扫描器在此同样容易误判）
    html = '<a title="see hint" href=/docs/guide.html?lang=zh#intro>x</a>'
    assert rewrite_html_for_site(html) == (
        '<a title="see hint" href=/site/docs/guide.html?lang=zh#intro>x</a>'
    )
    # HTML 实体、查询串、片段随真 href 原样保留
    html = "<a title=\"hint href='/x'\" href=\"/a.html?a=1&amp;b=2#f\">x</a>"
    assert rewrite_html_for_site(html) == (
        "<a title=\"hint href='/x'\" href=\"/site/a.html?a=1&amp;b=2#f\">x</a>"
    )


# ---------- HTTP 端到端 ----------

def test_01_publish_pages_with_absolute_insite_link(client):
    resp = client.post("/publish", json={
        "expected_revision": 0,
        "changes": [
            {"op": "upsert", "path": "/index.html", "content": INDEX_HTML},
            {"op": "upsert", "path": "/docs/guide.html", "content": GUIDE_HTML},
        ],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 1


def test_02_site_rewrites_only_absolute_insite_hrefs(client):
    resp = client.get("/site/index.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # 绝对站内 href 指向 /site 下，查询串与片段仍指向原目标
    assert '<a href="/site/docs/guide.html?lang=zh#intro">指南（绝对）</a>' in body
    assert 'href="/docs/guide.html?lang=zh#intro"' not in body
    assert '<a href="/site/">站点根</a>' in body
    # 相对链接、纯片段、纯查询、外部链接保持原样
    assert '<a href="docs/guide.html#intro">指南（相对）</a>' in body
    assert '<a href="#top">纯片段</a>' in body
    assert '<a href="?lang=en">纯查询</a>' in body
    assert '<a href="https://example.com/x">外链</a>' in body
    assert '<a href="//cdn.example.com/lib.js">协议相对</a>' in body
    assert '<a href="mailto:a@b.com">邮件</a>' in body
    # 非链接 HTML 保持原样
    assert '<img src="/docs/guide.html">' in body
    assert '<!-- <a href="/docs/guide.html">注释里的链接</a> -->' in body
    assert '<p>正文提到 /docs/guide.html 不改</p>' in body


def test_03_site_rewrites_absolute_href_on_subpage_too(client):
    body = client.get("/site/docs/guide.html").text
    assert '<a href="../index.html#top">home rel</a>' in body      # 相对不改
    assert '<a href="/site/index.html#top">home abs</a>' in body   # 绝对加前缀


def test_04_rewritten_href_is_followable_within_site(client):
    # 模拟浏览器点击：取出 /site HTML 中改写后的 href，去掉 #片段后请求应为 200
    body = client.get("/site/index.html").text
    m = re.search(r'href="(/site/[^"]+)"', body)
    assert m is not None
    url = m.group(1).split("#", 1)[0]  # 片段不发给服务器，查询串随 URL 原样携带
    assert url == "/site/docs/guide.html?lang=zh"
    resp = client.get(url)
    assert resp.status_code == 200
    assert '<h2 id="intro">Guide</h2>' in resp.text


def test_05_pages_api_and_history_keep_published_body(client):
    # /pages 当前集合与历史快照都是发布原文（不带 /site 前缀）
    current = client.get("/pages").json()
    assert current["pages"]["/index.html"] == INDEX_HTML
    assert current["pages"]["/docs/guide.html"] == GUIDE_HTML
    rev1 = client.get("/pages", params={"revision": 1}).json()
    assert rev1["pages"]["/index.html"] == INDEX_HTML
    assert rev1["pages"]["/docs/guide.html"] == GUIDE_HTML


def test_06_relocate_keeps_links_reachable_and_308_direct(client):
    resp = client.post("/relocate", json={
        "expected_revision": 1,
        "old_path": "/docs/guide.html",
        "new_path": "/manual/guide.html",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 2

    # /site 首页的链接跟随搬迁结果，指向 /site 下的新页面
    body = client.get("/site/index.html").text
    assert '<a href="/site/manual/guide.html?lang=zh#intro">指南（绝对）</a>' in body

    # 旧址保持一次 308 直达 /site 下的真实页（不形成跳转链）
    resp = client.get("/site/docs/guide.html", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/site/manual/guide.html"
    resp = client.get("/site/manual/guide.html")
    assert resp.status_code == 200
    assert '<h2 id="intro">Guide</h2>' in resp.text

    # 存储正文是搬迁改写结果（不带 /site 前缀）；历史快照保持搬迁前原文
    current = client.get("/pages").json()["pages"]
    assert '<a href="/manual/guide.html?lang=zh#intro">指南（绝对）</a>' in current["/index.html"]
    assert "/site/" not in current["/index.html"]
    assert "/docs/guide.html" not in current
    rev1 = client.get("/pages", params={"revision": 1}).json()["pages"]
    assert rev1["/index.html"] == INDEX_HTML
    assert rev1["/docs/guide.html"] == GUIDE_HTML


def test_07_unknown_paths_still_404(client):
    assert client.get("/site/unknown.html").status_code == 404
    # /site 挂载之外的路径按现有路由仍是 404（改写让浏览器不再落到这里）
    assert client.get("/docs/guide.html").status_code == 404
    assert client.get("/manual/guide.html").status_code == 404


# ---------- 回归：锚点其他属性值里的伪 href= ----------
#
# 既有 test_06 已把 /docs/guide.html 搬迁到 /manual/guide.html（revision=2，
# /docs/guide.html 现为跳转路径）。为演示题述的"伪 href="场景，这里使用
# 另一组未占用的页面路径（/start.html 与 /docs/target.html）。

BAIT_INDEX_HTML = (
    '<h1 id="top">起点</h1>\n'
    "<a title=\"hint href='/docs/bait.html'\" "
    'href="/docs/target.html?lang=zh#intro">进入目标</a>\n'
    '<a href="https://example.com/x">外链</a>\n'
    "<!-- <a href=\"/docs/commented.html\">注释</a> -->\n"
    "<p>正文提到 href='/docs/text.html' 不改</p>"
)
BAIT_TARGET_HTML = '<h2 id="intro">Target intro</h2><a href="/start.html#top">back</a>'
BAIT_START_PATH = "/start.html"
BAIT_TARGET_PATH = "/docs/target.html"
BAIT_MOVED_PATH = "/manual/target.html"


def test_08_publish_pages_with_fake_href_in_title(client):
    resp = client.post("/publish", json={
        "expected_revision": 2,
        "changes": [
            {"op": "upsert", "path": BAIT_START_PATH, "content": BAIT_INDEX_HTML},
            {"op": "upsert", "path": BAIT_TARGET_PATH, "content": BAIT_TARGET_HTML},
        ],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 3


def test_09_site_prefixes_only_real_href_and_keeps_title(client):
    body = client.get("/site/start.html").text
    # 真正的 href 被加上 /site 前缀，查询串与片段原文保留
    assert (
        "<a title=\"hint href='/docs/bait.html'\" "
        'href="/site/docs/target.html?lang=zh#intro">进入目标</a>'
    ) in body
    # title 里的伪 href= 原样保留、不被加前缀
    assert "href='/site/docs/bait.html'" not in body
    assert "href='/docs/bait.html'" in body
    # 外链、注释、正文一律不动
    assert '<a href="https://example.com/x">外链</a>' in body
    assert '<!-- <a href="/docs/commented.html">注释</a> -->' in body
    assert "<p>正文提到 href='/docs/text.html' 不改</p>" in body


def test_10_rewritten_real_href_is_clickable_to_200(client):
    # 模拟浏览器从 /site/start.html 点击真正的链接：直达目标页，200
    resp = client.get("/site/docs/target.html?lang=zh")
    assert resp.status_code == 200
    assert '<h2 id="intro">Target intro</h2>' in resp.text
    # title 里的伪地址不存在于真实页面集合，且从未被改写进 href
    assert client.get("/site/docs/bait.html").status_code == 404


def test_11_pages_body_and_snapshots_remain_published_original(client):
    current = client.get("/pages").json()
    assert current["revision"] == 3
    assert current["pages"][BAIT_START_PATH] == BAIT_INDEX_HTML
    assert current["pages"][BAIT_TARGET_PATH] == BAIT_TARGET_HTML
    # 历史快照同样是发布原文
    rev3 = client.get("/pages", params={"revision": 3}).json()
    assert rev3["pages"][BAIT_START_PATH] == BAIT_INDEX_HTML
    assert "/site/" not in rev3["pages"][BAIT_START_PATH]


def test_12_relocate_repoints_only_real_href_and_old_addr_308(client):
    resp = client.post("/relocate", json={
        "expected_revision": 3,
        "old_path": BAIT_TARGET_PATH,
        "new_path": BAIT_MOVED_PATH,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 4

    # 起点页真正的 href 改指新页（保留查询串与片段），title 原文不动
    pages = client.get("/pages").json()["pages"]
    assert (
        "<a title=\"hint href='/docs/bait.html'\" "
        'href="/manual/target.html?lang=zh#intro">进入目标</a>'
    ) in pages[BAIT_START_PATH]
    assert "href='/docs/bait.html'" in pages[BAIT_START_PATH]
    # 搬迁页正文与 id 保留
    assert pages[BAIT_MOVED_PATH] == BAIT_TARGET_HTML

    # /site 起点页链接到达新页（存储新路径 + /site 前缀）
    body = client.get("/site/start.html").text
    assert 'href="/site/manual/target.html?lang=zh#intro"' in body
    assert client.get("/site/manual/target.html?lang=zh").status_code == 200

    # 旧址一次 308 直达新页（不形成跳转链）
    resp = client.get("/site/docs/target.html", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/site/manual/target.html"

    # 历史快照（revision 3）仍是搬迁前发布原文
    rev3 = client.get("/pages", params={"revision": 3}).json()["pages"]
    assert rev3[BAIT_START_PATH] == BAIT_INDEX_HTML
    assert rev3[BAIT_TARGET_PATH] == BAIT_TARGET_HTML


def test_13_failed_relocate_changes_nothing(client):
    # 新路径被占用 -> 422；页面、跳转与修订号均不变
    resp = client.post("/relocate", json={
        "expected_revision": 4,
        "old_path": BAIT_MOVED_PATH,
        "new_path": "/index.html",
    })
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["reason"] == "new path already exists"
    assert client.get("/revision").json() == {"revision": 4}
    # 旧址 308 仍然有效，页面集合未被触碰
    resp = client.get("/site/docs/target.html", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/site/manual/target.html"
    pages = client.get("/pages").json()["pages"]
    assert BAIT_MOVED_PATH in pages and BAIT_TARGET_PATH not in pages
