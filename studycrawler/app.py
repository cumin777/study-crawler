"""组装：把配置、状态库、抓取器拼成一个可运行的整体。"""

from __future__ import annotations

import logging

from .config import AppConfig, Source
from .db import StateDB
from .engines import ENGINES, RunSummary
from .fetch import Fetcher

log = logging.getLogger("crawler")


class App:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.db = StateDB(cfg.settings.db_path)
        self.fetcher = Fetcher(cfg.settings)

    def run_source(self, src: Source) -> RunSummary:
        engine_cls = ENGINES.get(src.type)
        if engine_cls is None:
            return RunSummary(src.name, errors=[f"未知来源类型: {src.type}"])
        try:
            return engine_cls(self).run(src)
        except Exception as exc:  # 单个源挂掉不影响其他源
            log.exception("[%s] 运行异常", src.name)
            return RunSummary(src.name, errors=[repr(exc)])

    def run_all(self, names: list[str] | None = None) -> list[RunSummary]:
        """跑所有启用的来源；给了 names 就只跑指定的（且仍要求 enabled）。"""
        picked = [
            s for s in self.cfg.sources
            if s.enabled and (not names or s.name in names)
        ]
        if names:
            missing = set(names) - {s.name for s in picked}
            for name in sorted(missing):
                log.warning("来源不存在或未启用: %s", name)
        results = []
        for s in picked:
            log.info("== [%s] (%s) 开始 ==", s.name, s.type)
            r = self.run_source(s)
            log.info(
                "== [%s] 结束: 新增 %d, 跳过 %d, 错误 %d ==",
                s.name, r.new, r.skipped, len(r.errors),
            )
            for err in r.errors:
                log.warning("[%s] %s", s.name, err)
            results.append(r)
        return results
