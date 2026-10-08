"""命令行入口。

用法（在项目根目录）：
    python -m studycrawler init           生成 config.toml
    python -m studycrawler list           列出所有来源
    python -m studycrawler crawl          抓取一轮（所有启用的源）
    python -m studycrawler crawl 图包站1  只抓指定来源
    python -m studycrawler watch          常驻增量抓取
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


def cmd_watch(args) -> None:
    cfg = load_config(args.config)
    app = App(cfg)
    interval = max(1, cfg.settings.watch_interval_min) * 60
    log = logging.getLogger("crawler")
    log.info("watch 模式启动，每 %d 分钟一轮，Ctrl+C 退出",
             cfg.settings.watch_interval_min)
    while True:
        try:
            app.run_all()
        except KeyboardInterrupt:
            log.info("收到退出信号，再见")
            return
        except Exception:
            log.exception("本轮出现意外错误，继续下一轮")
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
    sub.add_parser("watch", help="常驻增量抓取")
    args = parser.parse_args(argv)

    setup_logging()
    {"init": cmd_init, "list": cmd_list,
     "crawl": cmd_crawl, "watch": cmd_watch}[args.command](args)


if __name__ == "__main__":
    main()
