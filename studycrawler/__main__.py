"""命令行入口。

用法（在项目根目录）：
    python -m studycrawler init           生成 config.toml
    python -m studycrawler list           列出所有来源
    python -m studycrawler crawl          抓取一轮（所有启用的源）
    python -m studycrawler crawl 图包站1  只抓指定来源
    python -m studycrawler hunt "关键词"  关键词找站（--files/--out 可选）
    python -m studycrawler watch          常驻：sources + keywords.txt 新关键词
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .app import App
from .config import DEFAULT_CONFIG_TOML, PROJECT_ROOT, load_config


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        log_path = PROJECT_ROOT / "data" / "crawler.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except OSError:
        pass  # 日志文件写不进去就算了，控制台还能看
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=handlers,
    )


def cmd_init(args) -> None:
    cfg_path = PROJECT_ROOT / "config.toml"
    if cfg_path.exists():
        print(f"已存在 {cfg_path}，不覆盖")
        return
    cfg_path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    print(f"已生成 {cfg_path}")
    print("编辑它：填 sync_dir 和真实来源 URL，把 enabled 改成 true")


def cmd_list(args) -> None:
    cfg = load_config(args.config)
    print(f"{'名称':<16} {'类型':<9} {'启用':<4} {'分类':<8} URL")
    for s in cfg.sources:
        print(
            f"{s.name:<16} {s.type:<9} "
            f"{'是' if s.enabled else '否':<4} {s.category:<8} {s.url}"
        )


def cmd_crawl(args) -> None:
    cfg = load_config(args.config)
    app = App(cfg)
    results = app.run_all(args.names or None)
    total_new = sum(r.new for r in results)
    total_err = sum(len(r.errors) for r in results)
    print(f"\n本轮完成: 新增 {total_new}, 错误 {total_err}")


def cmd_hunt(args) -> None:
    from .hunt import Hunt

    cfg = load_config(args.config)
    app = App(cfg)
    query = " ".join(args.query)
    report, results = Hunt(app).run(
        query, pages=args.pages, top=args.sites,
        files_cap=args.files, out=args.out,
    )
    if args.adopt:
        cfg_path = args.config or PROJECT_ROOT / "config.toml"
        Hunt(app).adopt(results, Path(cfg_path))


def cmd_check(args) -> None:
    """校验索引里的分享链接死活，把结果标注进索引文件。"""
    from .checker import check, parse_line, render_line, status_label

    cfg = load_config(args.config)
    app = App(cfg)
    log = logging.getLogger("crawler")
    sync = Path(cfg.settings.sync_dir)
    files = sorted(sync.rglob("分享链接.md"))
    if args.keywords:
        files = [f for f in files if any(k in str(f) for k in args.keywords)]
    if not files:
        log.info("没找到任何 分享链接.md（先跑 hunt 收集）")
        return
    for idx in files:
        lines = idx.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        stats = {"ok": 0, "dead": 0, "unknown": 0}
        for ln in lines:
            rec = parse_line(ln)
            if rec is None:
                out.append(ln)
                continue
            if not args.recheck and (rec["status"] or "").startswith("有效"):
                stats["ok"] += 1
                out.append(render_line(rec))
                continue
            st, note = check(rec["url"], app.fetcher)
            rec["status"] = status_label(st, note)
            stats[st] += 1
            app.db.mark(rec["url"], "linkcheck", title=note, status=st)
            out.append(render_line(rec))
            log.info("%s -> %s", rec["url"][:70], rec["status"])
        idx.write_text("\n".join(out) + "\n", encoding="utf-8")
        log.info("[%s] 有效 %d / 失效 %d / 未验证 %d",
                 idx.relative_to(sync), stats["ok"], stats["dead"], stats["unknown"])


def _hunt_pending_keywords(app, kw_path: Path, files_cap: int, top: int, out) -> None:
    """读关键词文件，hunt 掉还没处理过的新关键词。文件被外部更新也能自动接上。"""
    from .hunt import Hunt, query_done, read_keywords

    log = logging.getLogger("crawler")
    keywords = read_keywords(kw_path)
    if not keywords:
        return
    pending = [q for q in keywords if not query_done(app, q)]
    log.info("关键词文件 %s: 共 %d 个，待处理 %d 个", kw_path, len(keywords), len(pending))
    hunter = Hunt(app)
    for q in pending:
        log.info("hunt 关键词: %s", q)
        try:
            hunter.run(q, top=top, files_cap=files_cap, out=out)
        except Exception:
            log.exception("hunt 关键词 %r 失败", q)


def cmd_watch(args) -> None:
    cfg = load_config(args.config)
    app = App(cfg)
    interval = max(1, cfg.settings.watch_interval_min) * 60
    kw_path = (
        Path(args.keywords) if args.keywords
        else PROJECT_ROOT / cfg.hunt.keywords_file
    )
    log = logging.getLogger("crawler")
    log.info("watch 模式启动，每 %d 分钟一轮（关键词文件: %s），Ctrl+C 退出",
             cfg.settings.watch_interval_min, kw_path)
    while True:
        try:
            app.run_all()
            _hunt_pending_keywords(app, kw_path, args.files, args.sites, args.out)
        except KeyboardInterrupt:
            log.info("收到退出信号，再见")
            return
        except Exception:
            log.exception("本轮出现意外错误，继续下一轮")
        if args.once:
            log.info("--once 单轮完成，退出")
            return
        nxt = datetime.now() + timedelta(seconds=interval)
        log.info("下一轮约 %s", nxt.strftime("%H:%M:%S"))
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("收到退出信号，再见")
            return


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="studycrawler", description="学习资料收集爬虫"
    )
    parser.add_argument("--config", type=Path, default=None,
                        help="指定 config.toml 路径（默认项目根目录）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="生成 config.toml")
    sub.add_parser("list", help="列出所有来源")
    p = sub.add_parser("crawl", help="抓取一轮")
    p.add_argument("names", nargs="*", help="要抓的来源名，留空=全部启用的")

    p = sub.add_parser("hunt", help="关键词找站：搜索->探测->收割")
    p.add_argument("query", nargs="+", help="关键词，如: 绘画 AI提效 claude")
    p.add_argument("--pages", type=int, default=2, help="每个引擎搜几页")
    p.add_argument("--sites", type=int, default=0, help="最多探测站点数（默认取配置）")
    p.add_argument("--files", type=int, default=20, help="本次最多下载的文件数")
    p.add_argument("--out", default=None, help="输出目录（默认 sync_dir/_hunt）")
    p.add_argument("--adopt", action="store_true",
                   help="把最优站自动转正为 config.toml 里的长期来源")

    p = sub.add_parser("check", help="校验分享链接死活并标注索引")
    p.add_argument("keywords", nargs="*", help="只校验路径含这些词的索引")
    p.add_argument("--recheck", action="store_true", help="已标有效的也重查")

    p = sub.add_parser("watch", help="常驻增量抓取（sources + 关键词文件）")
    p.add_argument("--keywords", type=Path, default=None,
                   help="关键词文件路径（默认项目根目录 keywords.txt）")
    p.add_argument("--files", type=int, default=20, help="每个关键词最多下载的文件数")
    p.add_argument("--sites", type=int, default=0, help="每个关键词最多探测站点数")
    p.add_argument("--out", default=None, help="关键词结果输出目录（默认 sync_dir/_hunt）")
    p.add_argument("--once", action="store_true", help="只跑一轮就退出")
    args = parser.parse_args(argv)

    setup_logging()
    {"init": cmd_init, "list": cmd_list, "crawl": cmd_crawl,
     "watch": cmd_watch, "hunt": cmd_hunt, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    main()
